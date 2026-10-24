"""Ingest and query logic, independent of HTTP so it can be tested directly."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .db import Database
from .hub import Hub
from .lineparse import ParseError, echo_summary, parse_echo, parse_shot_line, shot_from_dict
from .scoring import SCORING_VERSION, score_xy

log = logging.getLogger("fetserver.core")

EVENT_TYPES = {"shot", "config", "arduino_boot", "log"}
MAX_EVENTS_PER_BATCH = 50
MAX_RAW_CHARS = 16384
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
COMMAND_TYPES = {"serial", "wifi_portal", "reboot"}
# Only harmless Arduino commands may be queued. INIT, BYE, CAL, TEST, ... are deliberately absent.
ALLOWED_SERIAL = [
    re.compile(r'^\{"ECHO":0\}$'),
    re.compile(r'^\{"VERSION":0\}$'),
    re.compile(r'^\{"NEW":\d{1,5}\}$'),
]
MODES = ("practice", "sighter", "record")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PENDING_STATUSES = ("queued", "sent")
MAX_COMMANDS_PER_HEARTBEAT = 5
PARSE_ERRORS = (ParseError, KeyError, TypeError, ValueError, ArithmeticError)


class BadRequest(ValueError):
    pass


class NotFound(LookupError):
    pass


class Forbidden(PermissionError):
    pass


def _int(value, name: str, lo: int = 0, hi: int = 2**53) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise BadRequest(f"{name} must be an integer in {lo}..{hi}")
    return value


def _device_id(payload) -> str:
    did = payload.get("device_id") if isinstance(payload, dict) else None
    if not isinstance(did, str) or not DEVICE_ID_RE.match(did):
        raise BadRequest("device_id must be 1-64 characters of A-Z a-z 0-9 _ -")
    return did


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Core:
    def __init__(self, db: Database, now_ms: Optional[Callable[[], int]] = None,
                 offline_after_ms: int = 15000, heartbeat_interval_ms: int = 5000,
                 lease_ms: int = 60000, shot_lag_ms: int = 0, hub: Optional[Hub] = None):
        self.hub = hub or Hub()
        self.lease_ms = lease_ms
        self.shot_lag_ms = shot_lag_ms      # how long after detection a shot line reaches us (to be measured)
        self.db = db
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.offline_after_ms = offline_after_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms

    # ---- target bookkeeping -------------------------------------------------
    def _touch(self, c, device_id: str, recv_ms: int, **fields) -> None:
        c.execute("INSERT OR IGNORE INTO targets (device_id, first_seen_ms, last_seen_ms) VALUES (?,?,?)",
                  (device_id, recv_ms, recv_ms))
        fields = {k: v for k, v in fields.items() if v is not None}
        fields["last_seen_ms"] = recv_ms
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE targets SET {cols} WHERE device_id=?", (*fields.values(), device_id))

    @staticmethod
    def _register_boot(c, device_id: str, boot_count: int, boot_start_ms: int) -> None:
        # The smallest estimate is the least affected by network delay, so keep the minimum.
        c.execute("""INSERT INTO boots (device_id, boot_count, boot_start_ms) VALUES (?,?,?)
                     ON CONFLICT(device_id, boot_count)
                     DO UPDATE SET boot_start_ms = MIN(boot_start_ms, excluded.boot_start_ms)""",
                  (device_id, boot_count, boot_start_ms))

    @staticmethod
    def _boot_start(c, device_id: str, boot_count: int) -> Optional[int]:
        row = c.execute("SELECT boot_start_ms FROM boots WHERE device_id=? AND boot_count=?",
                        (device_id, boot_count)).fetchone()
        return None if row is None else row[0]

    # ---- POST /events -------------------------------------------------------
    def handle_events(self, payload, remote_ip: Optional[str] = None) -> dict:
        device_id = _device_id(payload)
        boot_count = _int(payload.get("boot_count"), "boot_count")
        uptime_ms = _int(payload.get("uptime_ms"), "uptime_ms")
        events = payload.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_BATCH:
            raise BadRequest(f"events must be a list of at most {MAX_EVENTS_PER_BATCH}")
        recv_ms = self.now_ms()
        acked, rejected = [], []
        created = False
        with self.db.write() as c:
            self._touch(c, device_id, recv_ms, last_ip=remote_ip, boot_count=boot_count, uptime_ms=uptime_ms)
            self._register_boot(c, device_id, boot_count, recv_ms - uptime_ms)
            for ev in events:
                try:
                    boot, seq, new_shot = self._store_event(c, device_id, boot_count, recv_ms, ev)
                    created = created or new_shot
                except BadRequest as exc:
                    rejected.append({"boot_count": ev.get("boot_count", boot_count) if isinstance(ev, dict) else None,
                                     "seq": ev.get("seq") if isinstance(ev, dict) else None, "reason": str(exc)})
                else:
                    acked.append([boot, seq])   # (boot_count, seq) is the event's identity
        if created:                         # after the commit, so a browser never sees an unsaved shot
            self.hub.publish({"type": "shot", "device_id": device_id})
        return {"acked": acked, "rejected": rejected, "server_time_ms": recv_ms}

    def _store_event(self, c, device_id: str, req_boot: int, recv_ms: int, ev):
        if not isinstance(ev, dict):
            raise BadRequest("event must be an object")
        seq = _int(ev.get("seq"), "seq")
        etype = ev.get("type")
        if etype not in EVENT_TYPES:
            raise BadRequest(f"unknown event type {etype!r}")
        ev_uptime = _int(ev.get("uptime_ms"), "uptime_ms")
        boot = _int(ev.get("boot_count", req_boot), "boot_count")
        raw = ev.get("raw")
        if raw is not None and (not isinstance(raw, str) or len(raw) > MAX_RAW_CHARS):
            raise BadRequest("raw must be a string of at most %d characters" % MAX_RAW_CHARS)
        data = ev.get("data")
        if data is not None and not isinstance(data, dict):
            raise BadRequest("data must be an object")

        start = self._boot_start(c, device_id, boot)
        if start is None:       # event from a boot we never heard from: time is a guess
            event_ms, quality = recv_ms, "unknown"
        else:
            event_ms, quality = start + ev_uptime, "ok"
            if etype == "shot":
                event_ms -= self.shot_lag_ms
        data_json = json.dumps(data) if data is not None else None
        cur = c.execute(
            """INSERT OR IGNORE INTO events
               (device_id, boot_count, seq, type, event_ms, time_quality, recv_ms, uptime_ms, raw, data_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (device_id, boot, seq, etype, event_ms, quality, recv_ms, ev_uptime, raw, data_json))
        new_shot = False
        if cur.rowcount == 1:   # new event
            new_shot = self._derive(c, cur.lastrowid, device_id, event_ms, etype, raw, data)
        else:                   # same identity seen before: a harmless retry, unless the content differs
            old = c.execute("SELECT raw, data_json FROM events WHERE device_id=? AND boot_count=? AND seq=?",
                            (device_id, boot, seq)).fetchone()
            if old is None or old["raw"] != raw or old["data_json"] != data_json:
                log.warning("event identity collision for %s boot %d seq %d: content differs, kept in event_conflicts",
                            device_id, boot, seq)
                c.execute("""INSERT INTO event_conflicts (device_id, boot_count, seq, type, recv_ms, uptime_ms, raw, data_json)
                             VALUES (?,?,?,?,?,?,?,?)""", (device_id, boot, seq, etype, recv_ms, ev_uptime, raw, data_json))
        return boot, seq, new_shot

    def _derive(self, c, event_id: int, device_id: str, event_ms: int, etype: str, raw, data) -> bool:
        """Returns True if a shot row was created."""
        try:
            if etype == "shot":
                self._derive_shot(c, event_id, device_id, event_ms, raw, data)
                return True
            elif etype == "config":
                d = parse_echo(raw) if raw else data
                self._apply_config(c, device_id, d)
            elif etype == "arduino_boot":
                m = re.search(r'"([^"]+)"', raw or "")
                if m:
                    c.execute("UPDATE targets SET arduino_version=? WHERE device_id=?", (m.group(1), device_id))
        except PARSE_ERRORS as exc:
            log.warning("event %d (%s) not parseable: %s", event_id, etype, exc)
            c.execute("UPDATE events SET parse_error=? WHERE id=?", (str(exc)[:300], event_id))
        return False

    def _derive_shot(self, c, event_id, device_id, event_ms, raw, data) -> None:
        sl = parse_shot_line(raw) if raw else shot_from_dict(data or {})
        t = c.execute("SELECT calibre_x10, align_x_h, align_y_h FROM targets WHERE device_id=?",
                      (device_id,)).fetchone()
        calibre, ax_off, ay_off = t["calibre_x10"], t["align_x_h"], t["align_y_h"]
        if sl.miss:
            ax = ay = None
            ring, tenths = 0, 0       # a miss is never a centre hit
        else:
            ax, ay = sl.x_h + ax_off, sl.y_h + ay_off
            sc = score_xy(ax, ay, calibre * 10)
            ring, tenths = sc.ring, sc.tenths
        # The mode in force when the shot happened (not when it arrived), so a delayed upload
        # lands in the right segment.
        seg = c.execute("""SELECT id FROM segments WHERE device_id=? AND started_ms<=?
                           AND (ended_ms IS NULL OR ended_ms>?) ORDER BY id DESC LIMIT 1""",
                        (device_id, event_ms, event_ms)).fetchone()
        c.execute(
            """INSERT INTO shots (event_id, device_id, event_ms, shot_number, is_miss, x_h, y_h, ax_h, ay_h,
                                  n, e, s, w, arduino_time_s, v_ref, temp_c, face, calibre_x10,
                                  scoring_version, ring, tenths, segment_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, device_id, event_ms, sl.shot, int(sl.miss), sl.x_h, sl.y_h, ax, ay,
             sl.n, sl.e, sl.s, sl.w, sl.time_s, sl.v_ref, sl.temp_c, sl.face, calibre,
             SCORING_VERSION, ring, tenths, seg["id"] if seg else None))

    def _apply_config(self, c, device_id: str, d: dict) -> None:
        if not isinstance(d, dict):
            raise ParseError("config is not an object")
        s = echo_summary(d)
        calibre = s["calibre_x10"] if isinstance(s["calibre_x10"], int) and 10 <= s["calibre_x10"] <= 100 else None
        sn = s["arduino_sn"] if isinstance(s["arduino_sn"], int) else None
        ver = s["arduino_version"] if isinstance(s["arduino_version"], str) else None
        self._touch(c, device_id, self.now_ms(), calibre_x10=calibre, arduino_sn=sn, arduino_version=ver,
                    config_json=json.dumps(d))

    # ---- POST /heartbeat ----------------------------------------------------
    def handle_heartbeat(self, payload, remote_ip: Optional[str] = None) -> dict:
        device_id = _device_id(payload)
        boot_count = _int(payload.get("boot_count"), "boot_count")
        uptime_ms = _int(payload.get("uptime_ms"), "uptime_ms")
        recv_ms = self.now_ms()
        name = payload.get("name")
        results = payload.get("cmd_results") or []
        if not isinstance(results, list):
            raise BadRequest("cmd_results must be a list")
        with self.db.write() as c:
            self._touch(c, device_id, recv_ms, last_ip=remote_ip, boot_count=boot_count, uptime_ms=uptime_ms,
                        name=name if isinstance(name, str) and name else None,
                        bridge_fw=_str(payload.get("bridge_fw")), arduino_version=_str(payload.get("arduino_version")),
                        rssi=payload.get("rssi") if isinstance(payload.get("rssi"), int) else None,
                        queue_depth=payload.get("queue_depth") if isinstance(payload.get("queue_depth"), int) else None,
                        overflow_count=payload.get("overflow_count") if isinstance(payload.get("overflow_count"), int) else None)
            self._register_boot(c, device_id, boot_count, recv_ms - uptime_ms)
            for r in results:
                if isinstance(r, dict) and isinstance(r.get("id"), int):
                    status = "done" if r.get("status") == "done" else "failed"
                    c.execute("""UPDATE commands SET status=?, acked_ms=?, result=?
                                 WHERE id=? AND device_id=? AND status IN ('queued','sent')""",
                              (status, recv_ms, _str(r.get("reply"), 500), r["id"], device_id))
            rows = c.execute("""SELECT id, type, line FROM commands WHERE device_id=? AND status IN ('queued','sent')
                                ORDER BY id LIMIT ?""", (device_id, MAX_COMMANDS_PER_HEARTBEAT)).fetchall()
            for r in rows:
                c.execute("UPDATE commands SET status='sent', sent_ms=COALESCE(sent_ms, ?) WHERE id=?", (recv_ms, r["id"]))
        return {"server_time_ms": recv_ms, "server_time": iso(recv_ms),
                "heartbeat_interval_ms": self.heartbeat_interval_ms,
                "commands": [{"id": r["id"], "type": r["type"], **({"line": r["line"]} if r["line"] else {})}
                             for r in rows]}

    # ---- commands (called by the browser side, later by the control feature) ----
    def enqueue_command(self, device_id: str, ctype: str, line: Optional[str] = None) -> int:
        if ctype not in COMMAND_TYPES:
            raise BadRequest(f"command type must be one of {sorted(COMMAND_TYPES)}")
        if ctype == "serial":
            if not isinstance(line, str) or not any(p.match(line) for p in ALLOWED_SERIAL):
                raise BadRequest("serial line is not on the allowlist")
        else:
            line = None
        with self.db.write() as c:
            if c.execute("SELECT 1 FROM targets WHERE device_id=?", (device_id,)).fetchone() is None:
                raise NotFound(f"unknown target {device_id}")
            cur = c.execute("INSERT INTO commands (device_id, type, line, created_ms) VALUES (?,?,?,?)",
                            (device_id, ctype, line, self.now_ms()))
            return cur.lastrowid

    # ---- control claim and modes -------------------------------------------
    @staticmethod
    def _check_client(client_id) -> str:
        if not isinstance(client_id, str) or not CLIENT_ID_RE.match(client_id):
            raise BadRequest("client_id must be 8-64 characters of A-Z a-z 0-9 _ -")
        return client_id

    @staticmethod
    def _lease_view(row, now: int, client_id: Optional[str] = None) -> dict:
        if row is None or row["expires_ms"] <= now:
            return {"held_by": None, "mine": False, "expires_in_s": 0}
        return {"held_by": row["holder_name"], "mine": client_id is not None and row["client_id"] == client_id,
                "expires_in_s": max(0, (row["expires_ms"] - now + 999) // 1000)}

    @staticmethod
    def _require_target(c, device_id: str) -> None:
        if c.execute("SELECT 1 FROM targets WHERE device_id=?", (device_id,)).fetchone() is None:
            raise NotFound(f"unknown target {device_id}")

    def acquire_control(self, device_id: str, client_id: str, name, takeover: bool = False) -> dict:
        """Claim (or renew) control. The claim expires unless renewed, so a closed browser frees the target."""
        self._check_client(client_id)
        name = name.strip()[:40] if isinstance(name, str) else ""
        if not name:
            raise BadRequest("name is required")
        now = self.now_ms()
        with self.db.write() as c:
            self._require_target(c, device_id)
            row = c.execute("SELECT * FROM leases WHERE device_id=?", (device_id,)).fetchone()
            free = row is None or row["expires_ms"] <= now
            mine = row is not None and row["client_id"] == client_id
            if not (free or mine or takeover):
                return {"granted": False, **self._lease_view(row, now, client_id)}
            changed = free or not mine or row["holder_name"] != name
            if mine and not free:
                c.execute("UPDATE leases SET holder_name=?, expires_ms=? WHERE device_id=?",
                          (name, now + self.lease_ms, device_id))
            else:
                c.execute("INSERT OR REPLACE INTO leases (device_id, client_id, holder_name, acquired_ms, expires_ms) "
                          "VALUES (?,?,?,?,?)", (device_id, client_id, name, now, now + self.lease_ms))
            row = c.execute("SELECT * FROM leases WHERE device_id=?", (device_id,)).fetchone()
        if changed:
            self.hub.publish({"type": "lease", "device_id": device_id})
        return {"granted": True, **self._lease_view(row, now, client_id)}

    def release_control(self, device_id: str, client_id: str) -> bool:
        self._check_client(client_id)
        with self.db.write() as c:
            cur = c.execute("DELETE FROM leases WHERE device_id=? AND client_id=?", (device_id, client_id))
            released = cur.rowcount == 1
        if released:
            self.hub.publish({"type": "lease", "device_id": device_id})
        return released

    def set_mode(self, device_id: str, client_id: str, mode, shooter=None, new_segment: bool = False) -> dict:
        """Switch practice / sighter / record. Only the current holder of the control claim may do this."""
        self._check_client(client_id)
        if mode not in MODES:
            raise BadRequest(f"mode must be one of {list(MODES)}")
        if shooter is not None:
            if not isinstance(shooter, str):
                raise BadRequest("shooter must be text")
            shooter = shooter.strip()[:60] or None
        now = self.now_ms()
        with self.db.write() as c:
            self._require_target(c, device_id)
            lease = c.execute("SELECT * FROM leases WHERE device_id=?", (device_id,)).fetchone()
            if lease is None or lease["expires_ms"] <= now or lease["client_id"] != client_id:
                raise Forbidden("take control of this target first")
            cur = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1",
                            (device_id,)).fetchone()
            if shooter is None:
                shooter = (cur["shooter"] if cur else None) or lease["holder_name"]
            c.execute("UPDATE leases SET expires_ms=? WHERE device_id=?", (now + self.lease_ms, device_id))
            if cur and cur["mode"] == mode and cur["shooter"] == shooter and not new_segment:
                seg_id = cur["id"]                       # nothing to change
                changed = False
            else:
                if cur:
                    c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, cur["id"]))
                seg_id = c.execute("INSERT INTO segments (device_id, mode, shooter, started_ms) VALUES (?,?,?,?)",
                                   (device_id, mode, shooter, now)).lastrowid
                changed = True
        if changed:
            self.hub.publish({"type": "mode", "device_id": device_id})
        return {"segment_id": seg_id, "mode": mode, "shooter": shooter, "started": iso(now)}

    # ---- queries ------------------------------------------------------------
    @staticmethod
    def _segment_stats(c, seg_id: int) -> dict:
        r = c.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(tenths),0) AS t, COALESCE(SUM(ring),0) AS ri,
                                COALESCE(SUM(is_miss),0) AS m FROM shots WHERE segment_id=?""", (seg_id,)).fetchone()
        return {"shots": r["n"], "misses": r["m"], "total_decimal": r["t"] / 10.0, "total_integer": r["ri"]}

    @staticmethod
    def _shot_brief(r) -> dict:
        miss = bool(r["is_miss"])
        return {"shot_number": r["shot_number"], "miss": miss, "ring": r["ring"], "decimal": r["tenths"] / 10.0,
                "x_mm": None if miss else r["ax_h"] / 100.0, "y_mm": None if miss else r["ay_h"] / 100.0}

    def list_targets(self, client_id: Optional[str] = None) -> list:
        now = self.now_ms()
        c = self.db.conn()
        rows = c.execute(
            """SELECT t.*, (SELECT COUNT(*) FROM shots s WHERE s.device_id=t.device_id) AS shot_count
               FROM targets t ORDER BY COALESCE(t.name, t.device_id), t.device_id""").fetchall()
        out = []
        for r in rows:
            did = r["device_id"]
            seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1",
                            (did,)).fetchone()
            if seg:
                recent = c.execute("SELECT * FROM shots WHERE segment_id=? ORDER BY id DESC LIMIT 10", (seg["id"],)).fetchall()
                stats = self._segment_stats(c, seg["id"])
            else:
                recent = c.execute("SELECT * FROM shots WHERE device_id=? ORDER BY id DESC LIMIT 10", (did,)).fetchall()
                stats = {"shots": 0, "misses": 0, "total_decimal": 0.0, "total_integer": 0}
            recent = [self._shot_brief(x) for x in reversed(recent)]
            lease = c.execute("SELECT * FROM leases WHERE device_id=?", (did,)).fetchone()
            out.append({
                "device_id": did, "name": r["name"] or did,
                "online": (now - r["last_seen_ms"]) <= self.offline_after_ms,
                "last_seen": iso(r["last_seen_ms"]), "last_seen_ms": r["last_seen_ms"],
                "arduino_version": r["arduino_version"], "arduino_sn": r["arduino_sn"],
                "bridge_fw": r["bridge_fw"], "rssi": r["rssi"], "queue_depth": r["queue_depth"],
                "overflow_count": r["overflow_count"], "ip": r["last_ip"], "shot_count": r["shot_count"],
                "calibre_x10": r["calibre_x10"],
                "mode": seg["mode"] if seg else "practice", "shooter": seg["shooter"] if seg else None,
                "segment_id": seg["id"] if seg else None, "segment": stats,
                "lease": self._lease_view(lease, now, client_id),
                "recent_shots": recent, "last_shot": recent[-1] if recent else None,
            })
        return out

    def list_segments(self, device_id: str, limit: int = 50) -> list:
        limit = max(1, min(int(limit), 500))
        c = self.db.conn()
        out = []
        for g in c.execute("SELECT * FROM segments WHERE device_id=? ORDER BY id DESC LIMIT ?", (device_id, limit)):
            out.append({"id": g["id"], "mode": g["mode"], "shooter": g["shooter"], "started": iso(g["started_ms"]),
                        "ended": iso(g["ended_ms"]) if g["ended_ms"] is not None else None,
                        "open": g["ended_ms"] is None, **self._segment_stats(c, g["id"])})
        return out

    def list_shots(self, device_id: str, since_id: int = 0, limit: int = 200, segment_id: Optional[int] = None) -> list:
        limit = max(1, min(int(limit), 1000))
        sql = """SELECT s.*, g.mode AS seg_mode, g.shooter AS seg_shooter FROM shots s
                 LEFT JOIN segments g ON g.id = s.segment_id WHERE s.device_id=? AND s.id>?"""
        args: list = [device_id, since_id]
        if segment_id is not None:
            sql += " AND s.segment_id=?"
            args.append(segment_id)
        rows = self.db.conn().execute(sql + " ORDER BY s.id LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            miss = bool(r["is_miss"])
            out.append({
                "id": r["id"], "time": iso(r["event_ms"]), "shot_number": r["shot_number"], "miss": miss,
                "x_mm": None if miss else r["ax_h"] / 100.0, "y_mm": None if miss else r["ay_h"] / 100.0,
                "raw_x_mm": None if miss else r["x_h"] / 100.0, "raw_y_mm": None if miss else r["y_h"] / 100.0,
                "ring": r["ring"], "decimal": r["tenths"] / 10.0,
                "counts": {"N": r["n"], "E": r["e"], "S": r["s"], "W": r["w"]},
                "scoring_version": r["scoring_version"],
                "segment_id": r["segment_id"], "mode": r["seg_mode"] or "practice", "shooter": r["seg_shooter"],
            })
        return out

    def conflict_count(self) -> int:
        return self.db.conn().execute("SELECT COUNT(*) FROM event_conflicts").fetchone()[0]

    # ---- maintenance --------------------------------------------------------
    def rescore(self) -> int:
        """Recompute scores from stored (aligned) coordinates. Raw events are never touched."""
        n = 0
        with self.db.write() as c:
            for r in c.execute("SELECT id, ax_h, ay_h, calibre_x10 FROM shots WHERE is_miss=0").fetchall():
                sc = score_xy(r["ax_h"], r["ay_h"], r["calibre_x10"] * 10)
                c.execute("UPDATE shots SET ring=?, tenths=?, scoring_version=? WHERE id=?",
                          (sc.ring, sc.tenths, SCORING_VERSION, r["id"]))
                n += 1
        return n


def _str(v, limit: int = 100) -> Optional[str]:
    return v[:limit] if isinstance(v, str) else None
