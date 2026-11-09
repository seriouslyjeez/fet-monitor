"""The link to deepTENS (docs/PROTOCOL.md, "The Monitor link"): linking this Pi to a range by a short code, its token
(kept in <data dir>/deeptens.json, mode 600, never in the database), the heartbeat that drives the deepTENS icon, and
the outbox: snapshots of matches and competitions that wait in the database until deepTENS acknowledges them. Nothing
here ever blocks shooting: the outbox is only written by the match code's own transactions, and everything that talks
to deepTENS runs in its own thread (run()), one step() at a time."""
from __future__ import annotations

import gzip
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlencode, urlparse

log = logging.getLogger("fetserver.deeptens")

DEFAULT_URL = "https://deeptens.com:8443"       # until deepTENS goes public (then https://deeptens.com)
API = "/api/monitor/v1"
HEARTBEAT_S = 30
CONNECTED_S = 90                                 # green while the last success is younger than this
ROSTER_S = 600                                   # the staff roster is fetched again after this long
ROSTER_MAX_AGE_S = 30 * 86400                    # offline sign-in stops working when the roster is older (P1 Q3)
SIGNIN_TTL_S = 600                               # a sign-in started on the Pi must come back within this long
LANES_S = 2                                      # the public lane board: at most this often, and only when changed
LANES_REFRESH_S = 30                             # ... or this often unchanged (so deepTENS knows the lanes are alive)
BACKOFF_MIN_S, BACKOFF_MAX_S = 2, 60
BATCH_ITEMS, BATCH_BYTES = 20, 7 << 20          # deepTENS takes 20 items, 8 MB unpacked
REQUEST_TIMEOUT_S = 20                           # each connect and each read


class LinkError(ValueError):
    pass


def check_url(url: str) -> str:
    """deepTENS's address: https, or http only to this machine (tests, a laptop). The token never crosses a network
    unencrypted."""
    u = urlparse((url or "").strip().rstrip("/"))
    if u.scheme not in ("https", "http") or not u.hostname or u.path or u.query or u.username:
        raise LinkError("the deepTENS address looks like https://deeptens.com:8443")
    if u.scheme == "http":
        try:
            local = ipaddress.ip_address(u.hostname).is_loopback
        except ValueError:
            local = u.hostname == "localhost"
        if not local:
            raise LinkError("the deepTENS address must start with https://")
    return u.geturl()


def http_post(url: str, path: str, body: dict, token: Optional[str] = None, compress: bool = False) -> Tuple[int, dict]:
    """POST JSON; (status, JSON answer). A network failure raises OSError. Certificates are checked (system store)."""
    data = json.dumps(body, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "FeT-Monitor"}
    if compress:
        data, headers["Content-Encoding"] = gzip.compress(data), "gzip"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
            status, raw = r.status, r.read(1 << 20)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(1 << 20)
    try:
        out = json.loads(raw or b"{}")
    except ValueError:
        out = {}
    return status, out if isinstance(out, dict) else {}


class DeepTens:
    def __init__(self, core, data_dir, post: Callable = http_post, clock: Callable[[], float] = time.time,
                 name: Callable[[], str] = lambda: "FeT Monitor", version: str = ""):
        self.core, self.post, self.clock, self.name, self.version = core, post, clock, name, version
        self.path = Path(data_dir) / "deeptens.json" if data_dir is not None else None
        self.lock = threading.Lock()
        self.conf = self._load()
        self.linking: Optional[dict] = None            # a link in progress: device_code, user_code, uri, until, interval
        self.last_ok = 0.0
        self.error = ""
        self.lane_conflicts = []
        self.backoff = 0
        self.next_hb = self.next_send = self.next_poll = 0.0
        self.wake = threading.Event()
        self.dirty = set()                              # (kind, id) changed since the last snapshot: "m" match, "c" competition
        self.roster_path = Path(data_dir) / "deeptens-roster.json" if data_dir is not None else None
        self.roster = self._load_roster()               # {"members": [...], "fetched_ms", "range"}: who may sign in, and how
        self.next_roster = 0.0
        self.signins = {}                               # state -> (where to, expires): sign-ins started here
        self.lane_board = False                         # the range's public lane board is on (deepTENS says, in heartbeats)
        self.next_lanes, self.lanes_sent_at, self.last_lanes = 0.0, 0.0, None

    # ---------------------------------------------------------------------------------------------- the settings file
    def _load(self) -> dict:
        try:
            out = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
        except (OSError, ValueError):
            log.exception("deeptens.json unreadable: not linked")
            out = {}
        return out if isinstance(out, dict) else {}

    def _save(self) -> None:
        if self.path is None:
            return
        tmp = self.path.with_name("deeptens.json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.conf, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def _load_roster(self) -> dict:
        try:
            r = json.loads(self.roster_path.read_text()) if self.roster_path and self.roster_path.exists() else {}
        except (OSError, ValueError):
            r = {}
        return r if isinstance(r, dict) and isinstance(r.get("members"), list) else {}

    def _save_roster(self) -> None:
        if self.roster_path is None:
            return
        tmp = self.roster_path.with_name("deeptens-roster.json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.roster, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.roster_path)

    @property
    def linked(self) -> bool:
        return bool(self.conf.get("token"))

    def status(self) -> dict:
        now = self.clock()
        with self.lock:
            lk = self.linking
            return {"linked": self.linked, "url": self.conf.get("url") or DEFAULT_URL, "range": self.conf.get("range"),
                    "connected": self.linked and now - self.last_ok < CONNECTED_S,
                    "last_ok_ms": int(self.last_ok * 1000) if self.last_ok else None, "error": self.error,
                    "outbox": self.outbox_count(), "rejected": self.rejected(), "lane_conflicts": self.lane_conflicts,
                    "unlinked_remotely": bool(self.conf.get("unlinked_remotely")), "roster_ok": self.roster_fresh(),
                    "roster_ms": self.roster.get("fetched_ms"), "staff": len(self.roster.get("members", [])),
                    "linking": {"user_code": lk["user_code"], "uri": lk["uri"], "uri_complete": lk["uri_complete"],
                                "expires_ms": int(lk["until"] * 1000)} if lk else None}

    # ------------------------------------------------------------------------------------------------------ linking
    def start_link(self, url: Optional[str] = None) -> dict:
        """Range Control → This Pi → Link to deepTENS: ask deepTENS for a code to show."""
        url = check_url(url or self.conf.get("url") or DEFAULT_URL)
        if self.linked:
            raise LinkError("already linked: unlink first")
        try:
            st, r = self.post(url, API + "/link", {"name": self.name(), "version": self.version})
        except OSError as exc:
            raise LinkError(f"deepTENS could not be reached ({exc})")
        if st != 200 or not isinstance(r.get("device_code"), str) or not isinstance(r.get("user_code"), str):
            raise LinkError(r.get("error") or f"deepTENS answered {st}")
        now = self.clock()
        with self.lock:
            self.conf = {"url": url}
            self._save()
            self.linking = {"device_code": r["device_code"], "user_code": r["user_code"], "uri": str(r.get("verification_uri", "")),
                            "uri_complete": str(r.get("verification_uri_complete", "")),
                            "until": now + min(int(r.get("expires_in") or 900), 3600), "interval": max(1, int(r.get("interval") or 5))}
            self.next_poll = now + self.linking["interval"]
            self.error = ""
        self.wake.set()
        return self.status()

    def cancel_link(self) -> None:
        with self.lock:
            self.linking = None

    def unlink(self) -> None:
        """Unlink from the Pi: tell deepTENS if it can be reached, and forget the token in any case."""
        token, url = self.conf.get("token"), self.conf.get("url")
        if token:
            try:
                self.post(url, API + "/unlink", {}, token)
            except OSError:
                log.warning("deepTENS not reached while unlinking: forgetting the token anyway (unlink it there too)")
        self._forget(remotely=False)

    def _forget(self, remotely: bool) -> None:
        with self.lock:
            self.conf = {"url": self.conf.get("url") or DEFAULT_URL, "unlinked_remotely": remotely}
            self._save()
            self.linking, self.last_ok, self.lane_conflicts = None, 0.0, []
            self.error = "unlinked on deepTENS" if remotely else ""
            self.roster = {}                            # nobody signs in through a link that is gone
            if self.roster_path is not None and self.roster_path.exists():
                self.roster_path.unlink()
        with self.core.db.write() as c:                 # what was waiting belonged to that link
            c.execute("DELETE FROM deeptens_outbox")
            c.execute("DELETE FROM deeptens_revs")

    # ------------------------------------------------------------------------------------------------------- outbox
    def outbox_count(self) -> int:
        return self.core.db.conn().execute("SELECT COUNT(*) FROM deeptens_outbox WHERE rejected IS NULL").fetchone()[0]

    def rejected(self) -> list:
        return [{"key": r["key"], "why": r["rejected"]} for r in
                self.core.db.conn().execute("SELECT key, rejected FROM deeptens_outbox WHERE rejected IS NOT NULL ORDER BY id")]

    @staticmethod
    def enqueue(c, kind: str, key: str, data: dict, now_ms: int) -> int:
        """Queue a snapshot in the caller's transaction. A newer snapshot of a key replaces one still waiting; its
        revision grows by one each time (deepTENS keeps the highest). Returns the revision."""
        r = c.execute("SELECT rev FROM deeptens_revs WHERE key=?", (key,)).fetchone()
        rev = (r["rev"] if r else 0) + 1
        c.execute("INSERT INTO deeptens_revs (key, rev) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET rev=excluded.rev", (key, rev))
        blob = json.dumps(data, separators=(",", ":"))
        if c.execute("UPDATE deeptens_outbox SET rev=?, data=?, queued_ms=?, rejected=NULL WHERE key=?", (rev, blob, now_ms, key)).rowcount == 0:
            c.execute("INSERT INTO deeptens_outbox (kind, key, rev, data, queued_ms) VALUES (?,?,?,?,?)", (kind, key, rev, blob, now_ms))
        return rev

    # -------------------------------------------------------------------------------------------- staff and sign-in
    def roster_fresh(self) -> bool:
        f = self.roster.get("fetched_ms")
        return self.linked and isinstance(f, (int, float)) and self.clock() * 1000 - f < ROSTER_MAX_AGE_S * 1000

    def member(self, user_id=None, email=None) -> Optional[dict]:
        """A staff member from the roster, by deepTENS id or email (any case); None if not there or the roster is stale."""
        if not self.roster_fresh():
            return None
        for p in self.roster.get("members", []):
            if (user_id is not None and p.get("user_id") == user_id) or (email and str(p.get("email", "")).lower() == email.strip().lower()):
                return p
        return None

    def still_has(self, user_id, role: str, since_ms: int) -> bool:
        """A session made through deepTENS stays valid until a roster fetched after it no longer gives the role."""
        f = self.roster.get("fetched_ms")
        if not self.linked:
            return False
        if not isinstance(f, (int, float)) or f <= since_ms:
            return True
        p = self.member(user_id=user_id)
        return bool(p) and role in p.get("roles", [])

    def start_signin(self, to: str, redirect_uri: str) -> str:
        """The deepTENS address to send a browser to for "Sign in with deepTENS" (to: range or match)."""
        if not self.linked or not self.conf.get("monitor_id"):
            raise LinkError("this Pi is not linked to deepTENS")
        state = secrets.token_urlsafe(24)
        now = self.clock()
        with self.lock:
            self.signins = {k: v for k, v in self.signins.items() if v[1] > now}
            self.signins[state] = (to, now + SIGNIN_TTL_S)
        return self.conf["url"] + "/monitor/signin/?" + urlencode({"monitor": self.conf["monitor_id"], "state": state, "redirect_uri": redirect_uri})

    def finish_signin(self, code: str, state: str) -> tuple:
        """(where to, {"user_id", "name", "email"}, roles) for a browser back from deepTENS; LinkError otherwise."""
        with self.lock:
            got = self.signins.pop(state, None) if isinstance(state, str) else None
        if got is None or got[1] <= self.clock():
            raise LinkError("this sign-in was not started here, or took too long: start again")
        if not isinstance(code, str) or not code:
            raise LinkError("deepTENS sent no sign-in code")
        try:
            st, r = self._call("/signin/exchange", {"code": code})
        except OSError as exc:
            raise LinkError(f"deepTENS could not be reached ({exc})")
        if st != 200 or not isinstance(r.get("user"), dict):
            raise LinkError(r.get("error") or f"deepTENS answered {st}")
        self.next_roster = 0                            # and bring the roster up to date
        self.wake.set()
        return got[0], r["user"], [x for x in r.get("roles", []) if isinstance(x, str)]

    def _fetch_roster(self, now: float) -> None:
        try:
            st, r = self._call("/roster", {})
        except OSError:
            self.next_roster = now + 60
            return
        if st == 401:
            self._forget(remotely=True)
            return
        if st == 200 and isinstance(r.get("members"), list):
            with self.lock:
                self.roster = {"members": r["members"], "range": r.get("range"), "fetched_ms": int(now * 1000)}
                self._save_roster()
            self.next_roster = now + ROSTER_S
        else:
            self.next_roster = now + 60

    # ------------------------------------------------------------------------------------------ the public lane board
    def lanes_payload(self) -> list:
        """Every target with a lane as the public board shows it (P1 Q2 answer 6). In a published match: its
        competitor and record shots (no sighters); in a match that is not published: no shots ("private"); otherwise
        its current shots, no name."""
        c = self.core.db.conn()
        out = []
        for t in self.core.list_targets():
            if t["lane"] is None:
                continue
            v, mi = t["view"], t["match"]
            f = v.get("face") or {}
            lane = {"lane": t["lane"], "online": t["online"],
                    "face": {"rings": [r["dia_mm"] for r in f.get("rings", [])], "black": f.get("black_mm") or 0,
                             "proj": f.get("projectile_mm") or 4.5, "decimal": bool(f.get("decimal"))}}
            if mi:
                m = c.execute("SELECT m.name, m.publish_deeptens, c.publish_deeptens AS cpub, c.name AS cname FROM matches m "
                              "LEFT JOIN competitions c ON c.id=m.competition_id WHERE m.id=?", (mi["match_id"],)).fetchone()
                published = m is not None and (m["publish_deeptens"] == 1 or m["cpub"] == 1)
                if not published:
                    out.append(dict(lane, state="private"))
                    continue
                record = t["mode"] not in ("sighter",)
                out.append(dict(lane, state="match", name=mi.get("competitor") or "", label=(m["cname"] or m["name"]) + ("" if record else " · sighters"),
                                shots=self._shots(v) if record else [], total=v["agg"]["decimal"] if record else None,
                                count=v["count"] if record else 0, x=v["agg"]["x"] if record else 0))
                continue
            if not v.get("series_id"):
                out.append(dict(lane, state="idle", shots=[], total=None, count=0, x=0))
                continue
            out.append(dict(lane, state="sighter" if t["mode"] == "sighter" else "practice", shots=self._shots(v),
                            total=v["agg"]["decimal"], count=v["count"], x=v["agg"]["x"]))
        return out

    @staticmethod
    def _shots(v) -> list:
        return [{"x": s["x_mm"], "y": s["y_mm"], "v": s["decimal"], "m": s["miss"]} for s in v.get("bull_shots", [])]

    def _send_lanes(self, now: float) -> None:
        self.next_lanes = now + LANES_S
        lanes = self.lanes_payload()
        if lanes == self.last_lanes and now - self.lanes_sent_at < LANES_REFRESH_S:
            return
        try:
            st, r = self._call("/lanes", {"lanes": lanes})
        except OSError:
            return                                      # live only: what is missed is not sent later
        if st == 200:
            self.last_lanes, self.lanes_sent_at = lanes, now
            self.lane_board = r.get("lane_board") is True

    # ---------------------------------------------------------------------------------------------------- snapshots
    def changed(self, kind: str, n: int) -> None:
        """A match ("m") or competition ("c") changed: a new snapshot is made in the link's thread (never in the
        request that changed it). Only while linked."""
        if self.linked:
            with self.lock:
                self.dirty.add((kind, n))
            self.wake.set()

    def snapshot(self, kind: str, n: int) -> Optional[tuple]:
        """(kind, key, data) to send for a change, or None. A relay or final is part of its competition. Matches and
        competitions that never started are not sent; one deleted after it was sent goes as {"deleted": true}."""
        from .matches import MatchError
        c = self.core.db.conn()
        if kind == "m":
            m = c.execute("SELECT * FROM matches WHERE id=?", (n,)).fetchone()
            if m is not None and m["competition_id"] is not None:
                kind, n = "c", m["competition_id"]
            elif m is None:
                return self._gone("match", f"m{n}")
            elif m["state"] == "ready":
                return None
            else:
                res = self.core.results.get(f"m{n}", with_shots=True, hidden_too=True)
                shown = not m["results_hidden"]
                return "match", f"m{n}", dict(res, state=m["state"], official=shown and bool(m["official_ms"]),
                                              publish=shown and m["publish_deeptens"] == 1)
        row = c.execute("SELECT * FROM competitions WHERE id=?", (n,)).fetchone()
        if row is None:
            return self._gone("competition", f"c{n}")
        if c.execute("SELECT 1 FROM matches WHERE competition_id=? AND state!='ready' LIMIT 1", (n,)).fetchone() is None:
            return None
        try:
            res = self.core.results.get(f"c{n}", with_shots=True, hidden_too=True)
        except MatchError:
            return None
        shown = not row["results_hidden"]
        return "competition", f"c{n}", dict(res, official=shown and bool(res.get("official_ms")), publish=shown and row["publish_deeptens"] == 1)

    def _gone(self, kind: str, key: str) -> Optional[tuple]:
        sent = self.core.db.conn().execute("SELECT 1 FROM deeptens_revs WHERE key=?", (key,)).fetchone()
        return (kind, key, {"deleted": True, "publish": False, "official": False, "classes": []}) if sent else None

    def _snapshots(self, now: float) -> None:
        with self.lock:
            todo, self.dirty = self.dirty, set()
        for kind, n in sorted(todo):
            try:
                snap = self.snapshot(kind, n)
                if snap is not None:
                    with self.core.db.write() as c:
                        self.enqueue(c, *snap, int(now * 1000))
            except Exception:                           # one broken snapshot must not stop the others (or the link)
                log.exception("deepTENS snapshot of %s%s", kind, n)

    # --------------------------------------------------------------------------------------------------------- work
    def _ok(self, now: float) -> None:
        self.last_ok, self.backoff, self.error = now, 0, ""

    def _failed(self, now: float, why: str) -> float:
        self.backoff = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self.backoff * 2))
        self.error = why
        return now + self.backoff

    def _call(self, path: str, body: dict, compress: bool = False):
        """(status, answer); a network failure raises OSError. A 401 means unlinked on deepTENS."""
        return self.post(self.conf["url"], API + path, body, self.conf["token"], compress)

    def step(self) -> float:
        """One round of work; returns how long to wait before the next."""
        now = self.clock()
        if self.linking:
            return self._poll(now)
        if not self.linked:
            return 5.0
        if self.dirty:
            self._snapshots(now)
        if now >= self.next_roster:
            self._fetch_roster(now)
            if not self.linked:
                return 1.0
        if now >= self.next_hb:
            if not self._heartbeat(now):
                if not self.linked:
                    return 1.0
                self.next_send = max(self.next_send, self.next_hb)   # deepTENS is not answering: uploads wait with it
        if now >= self.next_send and self.outbox_count():
            self._send(now)
        if self.lane_board and self.linked and now >= self.next_lanes and now - self.last_ok < CONNECTED_S:
            self._send_lanes(now)
        nxt = min(self.next_hb, self.next_send if self.outbox_count() else self.next_hb)
        if self.lane_board:
            nxt = min(nxt, self.next_lanes)
        return max(0.2, min(5.0, nxt - self.clock()))

    def _poll(self, now: float) -> float:
        lk = self.linking
        if now >= lk["until"]:
            with self.lock:
                self.linking, self.error = None, "the link code expired: start again"
            return 1.0
        if now < self.next_poll:
            return self.next_poll - now
        try:
            st, r = self.post(self.conf["url"], API + "/link/poll", {"device_code": lk["device_code"]})
        except OSError as exc:
            self.next_poll = self._failed(now, f"deepTENS not reached: {exc}")
            return self.next_poll - now
        s = r.get("status")
        if s == "approved" and isinstance(r.get("token"), str):
            with self.lock:
                self.conf = {"url": self.conf["url"], "token": r["token"], "monitor_id": r.get("monitor_id"),
                             "range": r.get("range"), "linked_ms": int(now * 1000)}
                self._save()
                self.linking = None
                self._ok(now)
                self.next_hb = self.next_send = now
            log.warning("linked to deepTENS: range %s", (r.get("range") or {}).get("name"))
            return 0.2
        if s in ("denied", "expired") or st == 410:
            with self.lock:
                self.linking, self.error = None, "the link was refused on deepTENS" if s == "denied" else "the link code expired: start again"
            return 1.0
        if s == "slow_down":
            lk["interval"] += 5
        self.next_poll = now + lk["interval"]
        return lk["interval"]

    def _lanes(self) -> list:
        return [{"lane": t["lane"], "name": t["name"], "online": t["online"]} for t in self.core.list_targets() if t["lane"] is not None]

    def _heartbeat(self, now: float) -> bool:
        body = {"version": self.version, "name": self.name(), "lanes": self._lanes(), "outbox": self.outbox_count(), "now_ms": int(now * 1000)}
        try:
            st, r = self._call("/heartbeat", body)
        except OSError as exc:
            self.next_hb = self._failed(now, f"deepTENS not reached: {exc}")
            return False
        if st == 401:
            log.warning("deepTENS says this Pi is not linked (unlinked there): forgetting the token")
            self._forget(remotely=True)
            return False
        if st != 200:
            self.next_hb = self._failed(now, f"deepTENS answered {st}")
            return False
        with self.lock:
            self._ok(now)
            self.lane_conflicts = [x for x in r.get("lane_conflicts", []) if isinstance(x, int)]
            self.lane_board = r.get("lane_board") is True
        self.next_hb = now + HEARTBEAT_S
        return True

    def _send(self, now: float) -> None:
        rows, size = [], 0
        for r in self.core.db.conn().execute("SELECT * FROM deeptens_outbox WHERE rejected IS NULL ORDER BY id LIMIT ?", (BATCH_ITEMS,)):
            size += len(r["data"])
            if rows and size > BATCH_BYTES:
                break
            rows.append(r)
        items = [{"id": r["id"], "kind": r["kind"], "key": r["key"], "rev": r["rev"], "data": json.loads(r["data"])} for r in rows]
        try:
            st, ans = self._call("/items", {"items": items}, compress=True)
        except OSError as exc:
            self.next_send = self._failed(now, f"deepTENS not reached: {exc}")
            return
        if st == 401:
            self._forget(remotely=True)
            return
        if st != 200:
            self.next_send = self._failed(now, f"deepTENS answered {st}")
            return
        sent = {r["id"]: r["rev"] for r in rows}
        with self.core.db.write() as c:
            for i in ans.get("acks", []):
                if i in sent:                          # only the revision that was sent: a newer one queued meanwhile stays
                    c.execute("DELETE FROM deeptens_outbox WHERE id=? AND rev=?", (i, sent[i]))
            for x in ans.get("rejected", []):
                if isinstance(x, dict) and x.get("id") in sent:
                    c.execute("UPDATE deeptens_outbox SET rejected=? WHERE id=? AND rev=?", (str(x.get("why", "rejected"))[:200], x["id"], sent[x["id"]]))
        with self.lock:
            self._ok(now)
        self.next_send = now

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception:                           # never let the link thread die; log once and carry on
                log.exception("deepTENS link")
                wait = 10.0
            self.wake.wait(wait)
            self.wake.clear()
