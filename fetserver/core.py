"""Ingest and query logic, independent of HTTP so it can be tested directly."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Optional

from .db import Database
from .groupstats import group_stats
from .hub import Hub
from .lineparse import ParseError, echo_summary, parse_echo, parse_shot_line, shot_from_dict
from .scoring import SCORING_VERSION, INNER_TEN_MIN_TENTHS, is_inner_ten, score_xy
from .sightadj import suggest
from .matches import Matches
from .competitions import Competitions
from .resultstore import ResultStore

log = logging.getLogger("fetserver.core")

EVENT_TYPES = {"shot", "config", "arduino_boot", "log"}
MAX_EVENTS_PER_BATCH = 50
MAX_RAW_CHARS = 16384
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
COMMAND_TYPES = {"serial", "wifi_portal", "reboot", "factory_reset"}    # queued from Range Control only
# Only harmless Arduino commands may be queued. INIT, BYE, CAL, TEST, ... are deliberately absent.
ALLOWED_SERIAL = [
    re.compile(r'^\{"ECHO":0\}$'),
    re.compile(r'^\{"VERSION":0\}$'),
    re.compile(r'^\{"NEW":\d{1,5}\}$'),
]
MODES = ("practice", "sighter", "record", "ammo")
KIND_OF_MODE = {"practice": "practice", "sighter": "match", "record": "match", "ammo": "ammo"}
PROJECTILE_MM = (1.0, 10.0)      # accepted range for the ammo-test projectile size
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PENDING_STATUSES = ("queued", "sent")
MAX_COMMANDS_PER_HEARTBEAT = 5
BOOT_SKEW_MS = 30_000        # more than any crystal drift: the device must have restarted under the same boot number
PARSE_ERRORS = (ParseError, KeyError, TypeError, ValueError, ArithmeticError)


class BadRequest(ValueError):
    pass


class NotFound(LookupError):
    pass


class Forbidden(PermissionError):
    pass


class Banned(Forbidden):
    """A target banned in Range Control: its reports are refused and nothing is stored."""


def _int(value, name: str, lo: int = 0, hi: int = 2**53) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise BadRequest(f"{name} must be an integer in {lo}..{hi}")
    return value


def _device_id(payload) -> str:
    did = payload.get("device_id") if isinstance(payload, dict) else None
    if not isinstance(did, str) or not DEVICE_ID_RE.match(did):
        raise BadRequest("device_id must be 1-64 characters of A-Z a-z 0-9 _ -")
    return did


def _flash(n: int) -> str:
    return f"{n >> 20} MB" if n >= 1 << 20 else f"{n >> 10} KB"


SESSION_KIND = {"practice": "Practice", "match": "Match"}
RANGE_NAME = "Range"          # who a Range Control change is credited to when nobody holds control
DEFAULT_SESSION_RE = re.compile(r"^(.+) \u2013 (Practice|Match)$")


def session_name(holder: str, kind: str) -> str:
    """Default session name: who took control and the kind, e.g. "Alice \u2013 Practice"."""
    return f"{holder} \u2013 {SESSION_KIND.get(kind, kind.title())}"


def _nonneg(v) -> int:
    """A count the target may or may not send: missing or nonsense -> 0 (so an older firmware reads as 'cannot')."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 31 else 0


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ammo_score(ax, ay, ref, projectile_h: int):
    """Ammo test scoring: the first shot is the reference and always 10.9; every other shot is
    scored by its distance from that first shot, on the normal rings with the test's projectile size."""
    if ref is None:
        return 10, 109
    sc = score_xy(ax - ref["ax_h"], ay - ref["ay_h"], projectile_h)
    return sc.ring, sc.tenths


def even_h(mm) -> int:
    """A projectile size in mm -> hundredths of a mm, rounded to an even number (scoring needs a whole radius)."""
    if isinstance(mm, bool) or not isinstance(mm, (int, float, str)):
        raise BadRequest("projectile_mm must be a number")
    try:
        v = float(mm)
    except ValueError:
        raise BadRequest("projectile_mm must be a number") from None
    if not (PROJECTILE_MM[0] <= v <= PROJECTILE_MM[1]):          # also rejects NaN
        raise BadRequest(f"projectile_mm must be between {PROJECTILE_MM[0]} and {PROJECTILE_MM[1]}")
    return 2 * int((Decimal(str(v)) * 50).to_integral_value(rounding=ROUND_HALF_UP))


class Core:
    def __init__(self, db: Database, now_ms: Optional[Callable[[], int]] = None,
                 offline_after_ms: int = 15000, heartbeat_interval_ms: int = 5000,
                 lease_ms: int = 60000, shot_lag_ms: int = 0, hub: Optional[Hub] = None,
                 idle_new_series_ms: int = 30 * 60 * 1000):
        self._skew_warned = set()
        self.idle_new_series_ms = idle_new_series_ms   # a practice series left idle this long starts afresh
        self.hub = hub or Hub()
        self.lease_ms = lease_ms
        self.shot_lag_ms = shot_lag_ms      # how long after detection a shot line reaches us (to be measured)
        self.db = db
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.offline_after_ms = offline_after_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.matches = Matches(self)
        self.competitions = Competitions(self)
        self.results = ResultStore(self)

    # ---- target bookkeeping -------------------------------------------------
    def _touch(self, c, device_id: str, recv_ms: int, **fields) -> None:
        row = c.execute("SELECT banned FROM targets WHERE device_id=?", (device_id,)).fetchone()
        if row is not None and row["banned"]:
            raise Banned(f"target {device_id} is banned")
        c.execute("INSERT OR IGNORE INTO targets (device_id, first_seen_ms, last_seen_ms) VALUES (?,?,?)",
                  (device_id, recv_ms, recv_ms))
        fields = {k: v for k, v in fields.items() if v is not None}
        fields["last_seen_ms"] = recv_ms
        fields["removed"] = 0                       # a removed target that reports again is back
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
                    boot, seq, new_shot = self._store_event(c, device_id, boot_count, recv_ms, ev, uptime_ms)
                    created = created or new_shot
                except BadRequest as exc:
                    rejected.append({"boot_count": ev.get("boot_count", boot_count) if isinstance(ev, dict) else None,
                                     "seq": ev.get("seq") if isinstance(ev, dict) else None, "reason": str(exc)})
                else:
                    acked.append([boot, seq])   # (boot_count, seq) is the event's identity
        if created:                         # after the commit, so a browser never sees an unsaved shot
            self.hub.publish({"type": "shot", "device_id": device_id})
        return {"acked": acked, "rejected": rejected, "server_time_ms": recv_ms}

    def _store_event(self, c, device_id: str, req_boot: int, recv_ms: int, ev, req_uptime_ms: int = 0):
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
        quality = "ok"
        if start is not None and boot == req_boot and abs((recv_ms - req_uptime_ms) - start) > BOOT_SKEW_MS:
            # The stored start of this boot disagrees with what the device says right now by more than any clock
            # drift could explain: the device restarted but reused the boot number (for example after its flash was
            # erased). Trust this request, which is always about the current run, and say so.
            if (device_id, boot) not in self._skew_warned:
                self._skew_warned.add((device_id, boot))
                log.warning("%s: boot %d was seen starting at %d but its uptime now implies %d; "
                            "the device reused a boot number. Timing taken from the current request.",
                            device_id, boot, start, recv_ms - req_uptime_ms)
            start, quality = recv_ms - req_uptime_ms, "reanchored"
            # Remember the corrected start, so events of this boot that arrive later (in a request from a newer boot)
            # are timed from the right run as well. The old run's own events are long delivered, or lost to the clash.
            c.execute("UPDATE boots SET boot_start_ms=? WHERE device_id=? AND boot_count=?", (start, device_id, boot))
        if start is None:       # event from a boot we never heard from: time is a guess
            event_ms, quality = recv_ms, "unknown"
        else:
            event_ms = start + ev_uptime
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
        else:
            ax, ay = sl.x_h + ax_off, sl.y_h + ay_off
        # The mode in force when the shot happened (not when it arrived), so a delayed upload
        # lands in the right segment and series.
        seg_id, series_id, stream, series_no = self._assign_series(c, device_id, event_ms)
        ser = c.execute("SELECT kind, projectile_h FROM series WHERE id=?", (series_id,)).fetchone() if series_id else None
        if sl.miss:
            ring, tenths = 0, 0       # a miss is never a centre hit
        elif ser is not None and ser["kind"] == "ammo":
            ref = c.execute("SELECT ax_h, ay_h FROM shots WHERE series_id=? AND is_miss=0 ORDER BY series_no, id LIMIT 1",
                            (series_id,)).fetchone()
            ring, tenths = ammo_score(ax, ay, ref, ser["projectile_h"])
        else:
            sc = score_xy(ax, ay, calibre * 10)
            ring, tenths = sc.ring, sc.tenths
        c.execute(
            """INSERT INTO shots (event_id, device_id, event_ms, shot_number, is_miss, x_h, y_h, ax_h, ay_h,
                                  n, e, s, w, arduino_time_s, v_ref, temp_c, face, calibre_x10,
                                  scoring_version, ring, tenths, segment_id, series_id, series_no, stream)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, device_id, event_ms, sl.shot, int(sl.miss), sl.x_h, sl.y_h, ax, ay,
             sl.n, sl.e, sl.s, sl.w, sl.time_s, sl.v_ref, sl.temp_c, sl.face, calibre,
             SCORING_VERSION, ring, tenths, seg_id, series_id, series_no, stream))

    # ---- series -------------------------------------------------------------
    @staticmethod
    def _new_series(c, device_id: str, kind: str, shooter, start_ms: int) -> int:
        t = c.execute("SELECT bull_size, group_size, total_practice, total_match, projectile_h FROM targets WHERE device_id=?",
                      (device_id,)).fetchone()
        total = 0 if kind == "ammo" else (t["total_match"] if kind == "match" else t["total_practice"])   # ammo: no limit
        return c.execute("""INSERT INTO series (device_id, kind, shooter, started_ms, bull_size, group_size, total, projectile_h)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (device_id, kind, shooter, start_ms, t["bull_size"], t["group_size"], total,
                          t["projectile_h"])).lastrowid

    @staticmethod
    def _new_segment(c, device_id: str, mode: str, shooter, series_id: int, start_ms: int) -> int:
        return c.execute("INSERT INTO segments (device_id, mode, shooter, started_ms, series_id) VALUES (?,?,?,?,?)",
                         (device_id, mode, shooter, start_ms, series_id)).lastrowid

    def _series_of(self, c, seg):
        """The series a segment belongs to (older databases have segments without one)."""
        if seg["series_id"] is None:
            kind = KIND_OF_MODE.get(seg["mode"], "match")
            sid = self._new_series(c, seg["device_id"], kind, seg["shooter"], seg["started_ms"])
            if seg["ended_ms"] is not None:
                c.execute("UPDATE series SET ended_ms=? WHERE id=?", (seg["ended_ms"], sid))
            c.execute("UPDATE segments SET series_id=? WHERE id=?", (sid, seg["id"]))
        else:
            sid = seg["series_id"]
        return c.execute("SELECT * FROM series WHERE id=?", (sid,)).fetchone()

    def _assign_series(self, c, device_id: str, event_ms: int):
        """Returns (segment_id, series_id, stream, series_no) for a shot fired at event_ms."""
        seg = c.execute("""SELECT * FROM segments WHERE device_id=? AND started_ms<=?
                           AND (ended_ms IS NULL OR ended_ms>?) ORDER BY id DESC LIMIT 1""",
                        (device_id, event_ms, event_ms)).fetchone()
        if seg is None:
            if c.execute("SELECT 1 FROM segments WHERE device_id=? AND ended_ms IS NULL", (device_id,)).fetchone():
                return None, None, "scored", None        # fired before the first recorded mode: left unassigned
            sid = self._new_series(c, device_id, "practice", None, event_ms)   # first shot on a never-configured target
            self._new_segment(c, device_id, "practice", None, sid, event_ms)
            seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL", (device_id,)).fetchone()
        ser = self._series_of(c, seg)
        stream = "sighter" if seg["mode"] == "sighter" else "scored"
        n = c.execute("SELECT COUNT(*) FROM shots WHERE series_id=? AND stream=?", (ser["id"], stream)).fetchone()[0]
        if seg["ended_ms"] is None:                      # only the live series rolls over, never a closed one
            last = c.execute("SELECT MAX(event_ms) FROM shots WHERE series_id=?", (ser["id"],)).fetchone()[0]
            # A match lane's series never rolls over: a shot beyond the record count stays in the match and is flagged.
            in_match = c.execute("SELECT 1 FROM match_lanes WHERE series_id=?", (ser["id"],)).fetchone() is not None
            full = stream == "scored" and ser["kind"] != "ammo" and n >= ser["total"] and not in_match
            idle = ser["kind"] == "practice" and last is not None and event_ms - last > self.idle_new_series_ms
            if full or idle:
                c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (event_ms, seg["id"]))
                c.execute("UPDATE series SET ended_ms=? WHERE id=?", (event_ms, ser["id"]))
                sid = self._new_series(c, device_id, ser["kind"], ser["shooter"], event_ms)
                self._new_segment(c, device_id, seg["mode"], seg["shooter"], sid, event_ms)
                seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL", (device_id,)).fetchone()
                ser = c.execute("SELECT * FROM series WHERE id=?", (sid,)).fetchone()
                n = 0
        return seg["id"], ser["id"], stream, n + 1

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
                        overflow_count=payload.get("overflow_count") if isinstance(payload.get("overflow_count"), int) else None,
                        ota_space=_nonneg(payload.get("ota_space")), flash_size=_nonneg(payload.get("flash_size")))
            self._register_boot(c, device_id, boot_count, recv_ms - uptime_ms)
            for r in results:
                if isinstance(r, dict) and isinstance(r.get("id"), int):
                    status = "done" if r.get("status") == "done" else "failed"
                    cur = c.execute("""UPDATE commands SET status=?, acked_ms=?, result=?
                                       WHERE id=? AND device_id=? AND status IN ('queued','sent')""",
                                    (status, recv_ms, _str(r.get("reply"), 500), r["id"], device_id))
                    if cur.rowcount and status == "done" and c.execute(
                            "SELECT type FROM commands WHERE id=?", (r["id"],)).fetchone()[0] == "factory_reset":
                        # the unit is erasing itself and will not report again: take it off the lists (history stays)
                        c.execute("UPDATE targets SET removed=1 WHERE device_id=?", (device_id,))
                        c.execute("DELETE FROM leases WHERE device_id=?", (device_id,))
            rows = c.execute("""SELECT id, type, line FROM commands WHERE device_id=? AND status IN ('queued','sent')
                                ORDER BY id LIMIT ?""", (device_id, MAX_COMMANDS_PER_HEARTBEAT)).fetchall()
            changed = bool(results)
            for r in rows:
                cur = c.execute("UPDATE commands SET status='sent', sent_ms=COALESCE(sent_ms, ?) WHERE id=? AND status='queued'",
                                (recv_ms, r["id"]))
                changed = changed or cur.rowcount > 0
        if changed:                                   # command progress (e.g. a firmware update) shows up live
            self.hub.publish({"type": "commands", "device_id": device_id})
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
        elif ctype == "factory_reset":
            line = "erase"                    # the bridge refuses a factory reset without this word
            locked = self.matches.lock_of(self.db.conn(), device_id)
            if locked:
                raise BadRequest(f"{device_id} is in the match '{locked}'")
        else:
            line = None
        with self.db.write() as c:
            if c.execute("SELECT 1 FROM targets WHERE device_id=?", (device_id,)).fetchone() is None:
                raise NotFound(f"unknown target {device_id}")
            cur = c.execute("INSERT INTO commands (device_id, type, line, created_ms) VALUES (?,?,?,?)",
                            (device_id, ctype, line, self.now_ms()))
            return cur.lastrowid

    def queue_firmware(self, device_id: str, image: dict) -> int:
        """Range Control: tell one target to fetch and install `image` (a FirmwareStore entry). Refused unless the target
        reported it can update itself (firmware 0.2.0+), the image fits and was built for the same flash size (a
        different layout would move the target's settings and queued shots out from under it)."""
        with self.db.write() as c:
            t = c.execute("SELECT * FROM targets WHERE device_id=? AND removed=0 AND banned=0", (device_id,)).fetchone()
            if t is None:
                raise NotFound(f"unknown target {device_id}")
            locked = self.matches.lock_of(c, device_id)
            if locked:
                raise BadRequest(f"{device_id} is in the match '{locked}': update it after the match")
            if not t["ota_space"]:
                raise BadRequest(f"{device_id} runs FeT Bridge firmware {t['bridge_fw'] or '?'}, which cannot update from the Pi: "
                                 "install 0.2.0 or newer by USB (or its /update page) once")
            if image["size"] > t["ota_space"]:
                raise BadRequest(f"{device_id} has room for {t['ota_space']} bytes; this image is {image['size']}")
            if t["flash_size"] and t["flash_size"] != image["flash_size"]:
                raise BadRequest(f"{device_id} runs a {_flash(t['flash_size'])} build; this image is a {_flash(image['flash_size'])} "
                                 "build. Installing it would lose the target's settings and queued shots: export the "
                                 "firmware with the matching Flash Size")
            busy = c.execute("SELECT 1 FROM commands WHERE device_id=? AND type='fw_update' AND status IN ('queued','sent')",
                             (device_id,)).fetchone()
            if busy:
                raise BadRequest(f"{device_id} already has a firmware update waiting")
            cur = c.execute("INSERT INTO commands (device_id, type, line, created_ms) VALUES (?,?,?,?)",
                            (device_id, "fw_update", f"{image['sha256']} {image['size']}", self.now_ms()))
        self.hub.publish({"type": "targets", "device_id": device_id})
        return cur.lastrowid

    def firmware_info(self, device_id: str) -> dict:
        """What a target reported about updating itself (for choosing its best image)."""
        r = self.db.conn().execute("SELECT bridge_fw, ota_space, flash_size FROM targets WHERE device_id=? AND removed=0 AND banned=0",
                                   (device_id,)).fetchone()
        if r is None:
            raise NotFound(f"unknown target {device_id}")
        return {"bridge_fw": r["bridge_fw"], "ota_space": r["ota_space"], "flash_size": r["flash_size"]}

    def firmware_jobs(self) -> dict:
        """The latest firmware update per target: {device_id: {status, result, sha256, created, ...}}."""
        out = {}
        for r in self.db.conn().execute(
                """SELECT * FROM commands WHERE id IN (SELECT MAX(id) FROM commands WHERE type='fw_update' GROUP BY device_id)"""):
            sha, _, size = (r["line"] or "").partition(" ")
            out[r["device_id"]] = {"id": r["id"], "status": r["status"], "result": r["result"], "sha256": sha,
                                   "size": int(size) if size.isdigit() else None, "created": iso(r["created_ms"]),
                                   "sent": iso(r["sent_ms"]) if r["sent_ms"] else None}
        return out

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
            moved = (free or not mine) and self._practice_to_holder(c, device_id, name, now)
        if changed:
            self.hub.publish({"type": "lease", "device_id": device_id})
        if moved:
            self.hub.publish({"type": "mode", "device_id": device_id})
        return {"granted": True, **self._lease_view(row, now, client_id)}

    def _practice_to_holder(self, c, device_id: str, name: str, now: int) -> bool:
        """A new holder takes over a live practice series that carries someone else's DEFAULT session name: an empty
        series is renamed, one with shots is closed and a fresh one starts, so nobody's shots land in another's
        session. Custom names, matches and ammo tests are left alone."""
        seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1",
                        (device_id,)).fetchone()
        ser = self._series_of(c, seg) if seg else None
        if ser is None or ser["ended_ms"] is not None or ser["kind"] != "practice":
            return False
        m = DEFAULT_SESSION_RE.match(ser["shooter"] or "")
        if not m or m.group(2) != "Practice" or m.group(1) == name:
            return False
        new = session_name(name, "practice")
        if c.execute("SELECT 1 FROM shots WHERE series_id=? LIMIT 1", (ser["id"],)).fetchone() is None:
            c.execute("UPDATE series SET shooter=? WHERE id=?", (new, ser["id"]))
            c.execute("UPDATE segments SET shooter=? WHERE id=?", (new, seg["id"]))
        else:
            c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, seg["id"]))
            c.execute("UPDATE series SET ended_ms=? WHERE id=?", (now, ser["id"]))
            sid = self._new_series(c, device_id, "practice", new, now)
            self._new_segment(c, device_id, seg["mode"], new, sid, now)
        return True

    def shooter_start_record(self, device_id: str, client_id) -> dict:
        """The shooter holding a lane's control claim starts record in an NRA-style sighting-and-record stage."""
        self._check_client(client_id)
        now = self.now_ms()
        c = self.db.conn()
        lease = c.execute("SELECT * FROM leases WHERE device_id=?", (device_id,)).fetchone()
        if lease is None or lease["expires_ms"] <= now or lease["client_id"] != client_id:
            raise Forbidden("take control of this target first")
        info = self.matches.target_info(c, device_id, now)
        if info is None:
            raise NotFound(f"{device_id} is not in a match")
        self.matches.command(info["match_id"], "start_record", lease["holder_name"], device_id=device_id)
        return self.matches.target_info(self.db.conn(), device_id, self.now_ms())

    def release_control(self, device_id: str, client_id: str) -> bool:
        self._check_client(client_id)
        with self.db.write() as c:
            cur = c.execute("DELETE FROM leases WHERE device_id=? AND client_id=?", (device_id, client_id))
            released = cur.rowcount == 1
        if released:
            self.hub.publish({"type": "lease", "device_id": device_id})
        return released

    def _require_lease(self, c, device_id: str, client_id: str, now: int):
        self._require_target(c, device_id)
        lease = c.execute("SELECT * FROM leases WHERE device_id=?", (device_id,)).fetchone()
        if lease is None or lease["expires_ms"] <= now or lease["client_id"] != client_id:
            raise Forbidden("take control of this target first")
        c.execute("UPDATE leases SET expires_ms=? WHERE device_id=?", (now + self.lease_ms, device_id))
        return lease

    def set_mode(self, device_id: str, client_id: str, mode, shooter=None, new_series: bool = False, label=None,
                 admin: bool = False) -> dict:
        """Switch practice / sighter / record / ammo test. Only the current holder of the control claim may do this.

        Practice is its own series. Sighter and record belong to a match series: moving between them
        stays in the same series, so record shots pick up where they left off. Changing between practice
        and match, changing the shooter, or asking for a new series starts a fresh series. An ammo test
        is its own open-ended series: the first shot is the reference (10.9) and the rest are measured from it.

        An ammo test has two names. The *shooter* is whoever was in control when the test started and does not
        change for the life of the test (a `shooter` argument is ignored). The *label* (ammo, lot) is free text
        that can be changed at any time without starting over; an empty label clears it.

        admin=True is Range Control: no control claim is needed (whoever holds one keeps it); default names use the
        holder's name if there is one, else RANGE_NAME."""
        if not admin:
            self._check_client(client_id)
        if mode not in MODES:
            raise BadRequest(f"mode must be one of {list(MODES)}")
        if shooter is not None:
            if not isinstance(shooter, str):
                raise BadRequest("shooter must be text")
            shooter = shooter.strip()[:60] or None
        if label is not None:
            if not isinstance(label, str):
                raise BadRequest("label must be text")
            label = label.strip()[:60] or ""
        now = self.now_ms()
        with self.db.write() as c:
            if admin:
                self._require_target(c, device_id)
                lease = c.execute("SELECT * FROM leases WHERE device_id=? AND expires_ms>?", (device_id, now)).fetchone()
                holder = lease["holder_name"] if lease else RANGE_NAME
            else:
                holder = self._require_lease(c, device_id, client_id, now)["holder_name"]
            locked = self.matches.lock_of(c, device_id)
            if locked:
                raise Forbidden(f"{device_id} is in the match '{locked}': Match Control runs it until the match ends")
            series_id, seg_id, shooter, changed = self._mode_change(c, device_id, mode, shooter, new_series, label, holder, now)
        if changed:
            self.hub.publish({"type": "mode", "device_id": device_id})
        return {"segment_id": seg_id, "series_id": series_id, "kind": KIND_OF_MODE.get(mode, "match"), "mode": mode,
                "shooter": shooter, "label": self.db.conn().execute("SELECT label FROM series WHERE id=?", (series_id,)).fetchone()[0],
                "started": iso(now)}

    def _set_mode_in(self, c, device_id: str, mode: str, session: str, now: int, new_series: bool = False) -> int:
        """Match Control's mode change, inside its own transaction (no claim, no lock check). Returns the series id."""
        series_id, _, _, changed = self._mode_change(c, device_id, mode, session, new_series, None, RANGE_NAME, now)
        if changed:
            self.hub.publish({"type": "mode", "device_id": device_id})
        return series_id

    def _move_series(self, c, from_dev: str, to_dev: str, series_id: int, now: int) -> None:
        """Match Control moved a shooter to another target: their series continues on the new target (in sighter mode,
        until the range officer says they are ready). The old target is left with no session: like a new target, it
        shows practice and its next shot starts a practice series. Shots fired on the old target before now stay in
        the series even if they arrive later (they are placed by event time)."""
        seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (from_dev,)).fetchone()
        if seg is not None and seg["series_id"] == series_id:
            c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, seg["id"]))
        other = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (to_dev,)).fetchone()
        if other is not None:                          # whatever the new target was doing ends now
            c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, other["id"]))
            if other["series_id"] is not None:
                c.execute("UPDATE series SET ended_ms=? WHERE id=? AND ended_ms IS NULL", (now, other["series_id"]))
        ser = c.execute("SELECT shooter FROM series WHERE id=?", (series_id,)).fetchone()
        c.execute("UPDATE series SET device_id=? WHERE id=?", (to_dev, series_id))
        self._new_segment(c, to_dev, "sighter", ser["shooter"], series_id, now)
        for d in (from_dev, to_dev):
            self.hub.publish({"type": "mode", "device_id": d})

    def _mode_change(self, c, device_id, mode, shooter, new_series, label, holder, now):
        kind = KIND_OF_MODE.get(mode, "match")
        seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1",
                        (device_id,)).fetchone()
        ser = self._series_of(c, seg) if seg else None
        live_ammo = kind == "ammo" and ser is not None and ser["ended_ms"] is None and ser["kind"] == "ammo" and not new_series
        if kind == "ammo":
            shooter = ser["shooter"] if live_ammo else holder   # fixed for the test; takes whoever holds control
        elif shooter is None:                       # a custom session name carries over; a default one follows the kind
            prev = ser["shooter"] if ser is not None and ser["kind"] != "ammo" else None
            shooter = prev if prev and not DEFAULT_SESSION_RE.match(prev) else session_name(holder, kind)
        if live_ammo or (ser is not None and ser["ended_ms"] is None and ser["kind"] == kind and kind != "ammo"
                         and ser["shooter"] == shooter and not new_series):
            series_id = ser["id"]
            changed = seg["mode"] != mode
            if changed:
                c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, seg["id"]))
                seg_id = self._new_segment(c, device_id, mode, shooter, series_id, now)
            else:
                seg_id = seg["id"]
            if kind == "ammo" and label is not None and (ser["label"] or "") != label:
                c.execute("UPDATE series SET label=? WHERE id=?", (label or None, series_id))
                changed = True
        else:
            if seg:
                c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, seg["id"]))
            if ser is not None and ser["ended_ms"] is None:
                c.execute("UPDATE series SET ended_ms=? WHERE id=?", (now, ser["id"]))
            series_id = self._new_series(c, device_id, kind, shooter, now)
            if kind == "ammo" and label:
                c.execute("UPDATE series SET label=? WHERE id=?", (label, series_id))
            seg_id = self._new_segment(c, device_id, mode, shooter, series_id, now)
            changed = True
        return series_id, seg_id, shooter, changed

    SETTING_LIMITS = {"bull_size": (1, 100), "group_size": (1, 100), "total_practice": (1, 1000), "total_match": (1, 1000)}
    SETTING_KEYS = tuple(SETTING_LIMITS) + ("projectile_mm",)

    def set_settings(self, device_id: str, client_id: str, **values) -> dict:
        """Shots per bull / per aggregate group / in total. Applies to the live series and to new ones."""
        self._check_client(client_id)
        clean = {}
        for k, v in values.items():
            if v is None:
                continue
            if k == "projectile_mm":
                clean["projectile_h"] = even_h(v)
                continue
            if k not in self.SETTING_LIMITS:
                raise BadRequest(f"unknown setting {k}")
            lo, hi = self.SETTING_LIMITS[k]
            clean[k] = _int(v, k, lo, hi)
        now = self.now_ms()
        with self.db.write() as c:
            self._require_lease(c, device_id, client_id, now)
            if clean:
                cols = ", ".join(f"{k}=?" for k in clean)
                c.execute(f"UPDATE targets SET {cols} WHERE device_id=?", (*clean.values(), device_id))
                live = c.execute("""SELECT s.* FROM series s JOIN segments g ON g.series_id=s.id
                                    WHERE g.device_id=? AND g.ended_ms IS NULL ORDER BY g.id DESC LIMIT 1""",
                                 (device_id,)).fetchone()
                if live is not None and live["kind"] == "ammo":
                    if "projectile_h" in clean:             # a different projectile re-scores the whole test
                        c.execute("UPDATE series SET projectile_h=? WHERE id=?", (clean["projectile_h"], live["id"]))
                        self._rescore_series(c, live["id"])
                elif live is not None:
                    key = "total_match" if live["kind"] == "match" else "total_practice"
                    c.execute("UPDATE series SET bull_size=COALESCE(?,bull_size), group_size=COALESCE(?,group_size), "
                              "total=COALESCE(?,total) WHERE id=?",
                              (clean.get("bull_size"), clean.get("group_size"), clean.get(key), live["id"]))
            t = c.execute("SELECT bull_size, group_size, total_practice, total_match, projectile_h FROM targets WHERE device_id=?",
                          (device_id,)).fetchone()
        self.hub.publish({"type": "mode", "device_id": device_id})
        return self._settings_view(t)

    @staticmethod
    def _settings_view(r) -> dict:
        return {"bull_size": r["bull_size"], "group_size": r["group_size"], "total_practice": r["total_practice"],
                "total_match": r["total_match"], "projectile_mm": r["projectile_h"] / 100.0}

    # ---- queries ------------------------------------------------------------
    @staticmethod
    def _segment_stats(c, seg_id: int) -> dict:
        r = c.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(tenths),0) AS t, COALESCE(SUM(ring),0) AS ri,
                                COALESCE(SUM(is_miss),0) AS m FROM shots WHERE segment_id=?""", (seg_id,)).fetchone()
        return {"shots": r["n"], "misses": r["m"], "total_decimal": r["t"] / 10.0, "total_integer": r["ri"]}

    @staticmethod
    def _brief(r, off=(0, 0)) -> dict:
        miss = bool(r["is_miss"])
        return {"id": r["id"], "n": r["series_no"] if r["series_no"] is not None else r["shot_number"],
                "shot_number": r["shot_number"], "miss": miss, "ring": r["ring"], "decimal": r["tenths"] / 10.0,
                "inner": (not miss) and is_inner_ten(r["tenths"]),
                "x_mm": None if miss else (r["ax_h"] - off[0]) / 100.0,
                "y_mm": None if miss else (r["ay_h"] - off[1]) / 100.0}

    @staticmethod
    def _aggregate(rows) -> dict:
        return {"decimal": sum(r["tenths"] for r in rows) / 10.0, "integer": sum(r["ring"] for r in rows),
                "x": sum(1 for r in rows if not r["is_miss"] and is_inner_ten(r["tenths"])),
                "misses": sum(1 for r in rows if r["is_miss"]), "shots": len(rows)}

    def _stream_view(self, c, ser, stream: str, bull=None, upto=None) -> dict:
        """Everything a screen needs about one stream (scored or sighter) of a series.

        upto=k shows the series as it stood after its first k shots (shot-by-shot review): the picture, the
        groups, the totals and the ammo-test statistics are all computed from those k shots only.
        full_count is always the real number of shots."""
        rows = c.execute("SELECT * FROM shots WHERE series_id=? AND stream=? ORDER BY series_no, id",
                         (ser["id"], stream)).fetchall()
        full = len(rows)
        if upto is not None:
            rows = rows[:max(0, upto)]
        view = self._stream_view_rows(c, ser, stream, rows, bull)
        view["full_count"] = full
        return view

    def _stream_view_rows(self, c, ser, stream: str, rows, bull=None) -> dict:
        if ser["kind"] == "ammo":
            return self._ammo_view(ser, rows)
        n, bs, gs = len(rows), ser["bull_size"], ser["group_size"]
        groups = []
        for i in range(0, n, gs):
            chunk = rows[i:i + gs]
            groups.append({"index": i // gs, "first": i + 1, "last": i + len(chunk), "complete": len(chunk) == gs,
                           **self._aggregate(chunk)})
        bull_count = max(1, -(-n // bs))
        idx = bull_count - 1 if bull is None else max(0, min(int(bull), bull_count - 1))
        return {"series_id": ser["id"], "kind": ser["kind"], "stream": stream, "shooter": ser["shooter"], "label": ser["label"],
                "open": ser["ended_ms"] is None, "count": n, "total": ser["total"] if stream == "scored" else None,
                "bull_size": bs, "group_size": gs, "agg": self._aggregate(rows), "groups": groups,
                "bull_index": idx, "bull_count": bull_count,
                "bull_shots": [self._brief(r) for r in rows[idx * bs:(idx + 1) * bs]],
                "last_shot": self._brief(rows[-1]) if rows else None}

    def _ammo_view(self, ser, rows) -> dict:
        """An ammo test: every shot is drawn relative to the first one, with the live group statistics."""
        ref = next((r for r in rows if not r["is_miss"]), None)
        off = (ref["ax_h"], ref["ay_h"]) if ref else (0, 0)
        pts = [(r["ax_h"] - off[0], r["ay_h"] - off[1]) for r in rows if not r["is_miss"]]
        n = len(rows)
        return {"series_id": ser["id"], "kind": "ammo", "stream": "scored", "shooter": ser["shooter"], "label": ser["label"],
                "open": ser["ended_ms"] is None, "count": n, "total": None,
                "bull_size": max(1, n), "group_size": max(1, n), "agg": self._aggregate(rows), "groups": [],
                "bull_index": 0, "bull_count": 1, "bull_shots": [self._brief(r, off) for r in rows],
                "last_shot": self._brief(rows[-1], off) if rows else None,
                "ammo": group_stats(pts, ser["projectile_h"]) | {"misses": sum(1 for r in rows if r["is_miss"])}}

    @staticmethod
    def _empty_view(t, kind: str = "practice") -> dict:
        zero = {"decimal": 0.0, "integer": 0, "x": 0, "misses": 0, "shots": 0}
        if kind == "ammo":
            return {"series_id": None, "kind": "ammo", "stream": "scored", "shooter": None, "label": None, "open": True, "count": 0,
                    "total": None, "bull_size": 1, "group_size": 1, "agg": zero, "groups": [], "bull_index": 0,
                    "bull_count": 1, "bull_shots": [], "last_shot": None, "full_count": 0,
                    "ammo": group_stats([], t["projectile_h"]) | {"misses": 0}}
        return {"series_id": None, "kind": kind, "stream": "scored", "shooter": None, "label": None, "open": True, "count": 0,
                "total": t["total_match"] if kind == "match" else t["total_practice"],
                "bull_size": t["bull_size"], "group_size": t["group_size"], "agg": zero, "groups": [],
                "bull_index": 0, "bull_count": 1, "bull_shots": [], "last_shot": None, "full_count": 0}

    def list_targets(self, client_id: Optional[str] = None) -> list:
        now = self.now_ms()
        c = self.db.conn()
        rows = c.execute(
            """SELECT t.*, (SELECT COUNT(*) FROM shots s WHERE s.device_id=t.device_id) AS shot_count
               FROM targets t WHERE t.removed=0 AND t.banned=0 ORDER BY COALESCE(t.name, t.device_id), t.device_id""").fetchall()
        out = []
        for r in rows:
            did = r["device_id"]
            seg = c.execute("SELECT * FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1",
                            (did,)).fetchone()
            ser = c.execute("SELECT * FROM series WHERE id=?", (seg["series_id"],)).fetchone() if seg and seg["series_id"] else None
            mode = seg["mode"] if seg else "practice"
            if ser is not None:
                view = self._stream_view(c, ser, "sighter" if mode == "sighter" else "scored")
            else:
                view = self._empty_view(r, KIND_OF_MODE.get(mode, "match"))
            lease = c.execute("SELECT * FROM leases WHERE device_id=?", (did,)).fetchone()
            out.append({
                "device_id": did, "name": r["name"] or did,
                "online": (now - r["last_seen_ms"]) <= self.offline_after_ms,
                "last_seen": iso(r["last_seen_ms"]), "last_seen_ms": r["last_seen_ms"],
                "arduino_version": r["arduino_version"], "arduino_sn": r["arduino_sn"],
                "bridge_fw": r["bridge_fw"], "rssi": r["rssi"], "queue_depth": r["queue_depth"],
                "overflow_count": r["overflow_count"], "ip": r["last_ip"], "shot_count": r["shot_count"],
                "calibre_x10": r["calibre_x10"],
                "mode": mode, "kind": view["kind"], "shooter": view["shooter"], "label": view["label"], "series_id": view["series_id"],
                "settings": self._settings_view(r),
                "lease": self._lease_view(lease, now, client_id), "view": view,
                "match": self._match_info(c, did, now),
            })
        return out

    # ---- Range Control (the HTTP layer checks the admin session) ------------
    def admin_view(self) -> dict:
        """Every target for Range Control, plus the banned ones (which no other screen shows)."""
        banned = [{"device_id": r["device_id"], "name": r["name"] or r["device_id"], "ip": r["last_ip"],
                   "last_seen": iso(r["last_seen_ms"]), "bridge_fw": r["bridge_fw"]}
                  for r in self.db.conn().execute("SELECT * FROM targets WHERE banned=1 ORDER BY COALESCE(name, device_id)")]
        targets = self.list_targets(None)
        jobs = self.firmware_jobs()
        for t in targets:
            r = self.db.conn().execute("SELECT ota_space, flash_size FROM targets WHERE device_id=?", (t["device_id"],)).fetchone()
            t["ota_space"], t["flash_size"] = r["ota_space"], r["flash_size"]
            t["fw_job"] = jobs.get(t["device_id"])
        return {"targets": targets, "banned": banned}

    def remove_target(self, device_id: str) -> None:
        """Hide a dead target. Its history stays; if it ever reports again it is back automatically."""
        with self.db.write() as c:
            self._require_target(c, device_id)
            locked = self.matches.lock_of(c, device_id)
            if locked:
                raise BadRequest(f"{device_id} is in the match '{locked}'")
            c.execute("UPDATE targets SET removed=1 WHERE device_id=?", (device_id,))
            c.execute("DELETE FROM leases WHERE device_id=?", (device_id,))
        self.hub.publish({"type": "targets", "device_id": device_id})

    def set_banned(self, device_id: str, banned: bool) -> None:
        """A banned target's reports are refused (403) and nothing it sends is stored; unbanning lets it back."""
        with self.db.write() as c:
            self._require_target(c, device_id)
            locked = self.matches.lock_of(c, device_id) if banned else None
            if locked:
                raise BadRequest(f"{device_id} is in the match '{locked}'")
            c.execute("UPDATE targets SET banned=?, removed=0 WHERE device_id=?", (int(bool(banned)), device_id))
            if banned:
                c.execute("DELETE FROM leases WHERE device_id=?", (device_id,))
        self.hub.publish({"type": "targets", "device_id": device_id})

    def _match_info(self, c, device_id: str, now: int):
        """A target's match clock for its screen. Never allowed to break the target list: on an error the target shows
        without it (and the error is logged), so scoring screens keep working whatever happens in Match Control."""
        try:
            return self.matches.target_info(c, device_id, now)
        except Exception:
            log.exception("match info for %s", device_id)
            return None

    def list_series(self, device_id: str, limit: int = 50) -> list:
        limit = max(1, min(int(limit), 500))
        c = self.db.conn()
        out = []
        for g in c.execute("SELECT * FROM series WHERE device_id=? ORDER BY id DESC LIMIT ?", (device_id, limit)).fetchall():
            item = {"id": g["id"], "kind": g["kind"], "shooter": g["shooter"], "label": g["label"], "started": iso(g["started_ms"]),
                    "ended": iso(g["ended_ms"]) if g["ended_ms"] is not None else None, "open": g["ended_ms"] is None,
                    "bull_size": g["bull_size"], "group_size": g["group_size"], "total": g["total"]}
            for stream in ("scored", "sighter"):
                rows = c.execute("SELECT tenths, ring, is_miss FROM shots WHERE series_id=? AND stream=?",
                                 (g["id"], stream)).fetchall()
                item[stream] = self._aggregate(rows)
            if g["kind"] == "ammo":
                item["ammo"] = self._stream_view(c, g, "scored")["ammo"]
            out.append(item)
        return out

    def series_view(self, series_id: int, stream: str = "scored", bull=None, upto=None) -> dict:
        if stream not in ("scored", "sighter"):
            raise BadRequest("stream must be scored or sighter")
        c = self.db.conn()
        ser = c.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        if ser is None:
            raise NotFound(f"unknown series {series_id}")
        view = self._stream_view(c, ser, stream, bull, upto)
        view["started"] = iso(ser["started_ms"])
        view["ended"] = iso(ser["ended_ms"]) if ser["ended_ms"] is not None else None
        view["device_id"] = ser["device_id"]
        return view

    def sight_suggestion(self, series_id: int, after: int = 0) -> dict:
        """Sight adjustment for a practice series, from its shots numbered above `after` (the last adjustment)."""
        c = self.db.conn()
        ser = c.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        if ser is None:
            raise NotFound(f"unknown series {series_id}")
        if ser["kind"] != "practice":
            raise BadRequest("sight adjustment is only for practice series")
        rows = c.execute("SELECT series_no, is_miss, ax_h, ay_h FROM shots WHERE series_id=? AND stream='scored' AND series_no>? "
                         "ORDER BY series_no, id", (series_id, int(after))).fetchall()
        out = suggest([{"n": r["series_no"], "miss": bool(r["is_miss"]), "x_h": r["ax_h"], "y_h": r["ay_h"]} for r in rows])
        out.update({"series_id": series_id, "after": int(after)})
        return out

    @staticmethod
    def _segment_stats(c, seg_id: int) -> dict:
        r = c.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(tenths),0) AS t, COALESCE(SUM(ring),0) AS ri,
                                COALESCE(SUM(is_miss),0) AS m FROM shots WHERE segment_id=?""", (seg_id,)).fetchone()
        return {"shots": r["n"], "misses": r["m"], "total_decimal": r["t"] / 10.0, "total_integer": r["ri"]}

    def list_segments(self, device_id: str, limit: int = 50) -> list:
        limit = max(1, min(int(limit), 500))
        c = self.db.conn()
        out = []
        for g in c.execute("SELECT * FROM segments WHERE device_id=? ORDER BY id DESC LIMIT ?", (device_id, limit)):
            out.append({"id": g["id"], "series_id": g["series_id"], "mode": g["mode"], "shooter": g["shooter"],
                        "started": iso(g["started_ms"]),
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
                "segment_id": r["segment_id"], "series_id": r["series_id"], "series_no": r["series_no"],
                "stream": r["stream"], "inner": (not miss) and is_inner_ten(r["tenths"]),
                "mode": r["seg_mode"] or "practice", "shooter": r["seg_shooter"],
            })
        return out

    def conflict_count(self) -> int:
        return self.db.conn().execute("SELECT COUNT(*) FROM event_conflicts").fetchone()[0]

    # ---- maintenance --------------------------------------------------------
    def rescore(self) -> int:
        """Recompute scores from stored (aligned) coordinates. Raw events are never touched."""
        n = 0
        with self.db.write() as c:
            for r in c.execute("""SELECT s.id, s.ax_h, s.ay_h, s.calibre_x10 FROM shots s LEFT JOIN series g ON g.id=s.series_id
                                  WHERE s.is_miss=0 AND COALESCE(g.kind,'') != 'ammo'""").fetchall():
                sc = score_xy(r["ax_h"], r["ay_h"], r["calibre_x10"] * 10)
                c.execute("UPDATE shots SET ring=?, tenths=?, scoring_version=? WHERE id=?",
                          (sc.ring, sc.tenths, SCORING_VERSION, r["id"]))
                n += 1
            for g in c.execute("SELECT id FROM series WHERE kind='ammo'").fetchall():
                n += self._rescore_series(c, g["id"])
        return n

    @staticmethod
    def _rescore_series(c, series_id: int) -> int:
        """Ammo test scores depend on the first shot and the projectile size, so they are recomputed together."""
        ser = c.execute("SELECT projectile_h FROM series WHERE id=?", (series_id,)).fetchone()
        ref = None
        n = 0
        for r in c.execute("SELECT id, ax_h, ay_h FROM shots WHERE series_id=? AND is_miss=0 ORDER BY series_no, id",
                           (series_id,)).fetchall():
            if ref is None:
                ref = r
            ring, tenths = ammo_score(r["ax_h"], r["ay_h"], None if r is ref else ref, ser["projectile_h"])
            c.execute("UPDATE shots SET ring=?, tenths=?, scoring_version=? WHERE id=?", (ring, tenths, SCORING_VERSION, r["id"]))
            n += 1
        return n


def _str(v, limit: int = 100) -> Optional[str]:
    return v[:limit] if isinstance(v, str) else None
