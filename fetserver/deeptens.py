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
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("fetserver.deeptens")

DEFAULT_URL = "https://deeptens.com:8443"       # until deepTENS goes public (then https://deeptens.com)
API = "/api/monitor/v1"
HEARTBEAT_S = 30
CONNECTED_S = 90                                 # green while the last success is younger than this
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
                    "unlinked_remotely": bool(self.conf.get("unlinked_remotely")),
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
        if now >= self.next_hb:
            if not self._heartbeat(now):
                if not self.linked:
                    return 1.0
                self.next_send = max(self.next_send, self.next_hb)   # deepTENS is not answering: uploads wait with it
        if now >= self.next_send and self.outbox_count():
            self._send(now)
        nxt = min(self.next_hb, self.next_send if self.outbox_count() else self.next_hb)
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
