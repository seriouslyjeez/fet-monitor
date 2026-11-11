"""Target sign-in (P2, docs/PROTOCOL.md "Target sign-in"): a deepTENS athlete signed in at a lane, by their phone (the
lane's QR code opens deepTENS; this Pi takes it from its link), by athlete number and PIN on the lane's screen (checked
by deepTENS while online; offline against the range's athletes in the roster), or by range staff. One athlete per
target and one target per athlete. Shots fired while signed in (by event time) are theirs: the session goes to
deepTENS through the outbox. Signed out after the range's idle time without a shot, after 4 hours, never during a
match. Only while this Pi is linked: an athlete is a deepTENS athlete."""
from __future__ import annotations

import json
import secrets
import threading
from typing import Optional

from fetcore.matchengine import ACTIVE_SQL

from fetcore import faces as F
from .admin import verify_password

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LIFE_MS = 30 * 60 * 1000                    # a lane's QR code changes at least this often (and at each sign-in/out)
LIMIT_MS = 4 * 3600 * 1000                       # the hard limit (user, 2026-11-05)
IDLE_MIN_DEFAULT = 15
LATE_MS = 15 * 60 * 1000                         # a session's late shots (event time inside, arrived after) are sent up to this long after it ends
PIN_FAILS, PIN_LOCK_MS = 5, 15 * 60 * 1000       # offline (and per lane) on top of deepTENS's own lock
PIN_TIMEOUT_S = 6                                # a lane tablet does not wait longer for deepTENS: then offline
VIAS = ("phone", "pin", "pin_offline", "staff", "poster")
CLAIM_MS = 2 * 60 * 1000                         # a poster's claim waits this long for a shot on the lane (user, 2026-11-11)
CLAIM_GRACE_MS = 30 * 1000                       # a shot fired in time that arrives a little late still confirms it
CLAIM_QUIET_MS = 5 * 60 * 1000                   # a lane is free to claim only with no shot on it for this long


class SigninError(ValueError):
    pass


class Athletes:
    def __init__(self, core):
        self.core = core
        self.lock = threading.Lock()
        self.codes = {}                          # device_id -> {"code", "prev", "made_ms"}
        self.fails = {}                          # athlete number -> [count, locked until ms]
        self.sent = {}                           # session id -> what was last queued (shots, last event, ended)
        self.claims = {}                         # device_id -> a poster's claim waiting for a shot (docs/PLAN-signin-posters.md)
        self.claim_news = []                     # how claims went since the link last asked: {"id", "ok", "state", "why"}

    # ------------------------------------------------------------------------------------------------- lane codes
    def _new_code(self) -> str:
        return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))

    def code(self, device_id: str, now_ms: int) -> str:
        """The lane's current code for its QR (a new one at least every 30 minutes; the previous one still works)."""
        with self.lock:
            c = self.codes.get(device_id)
            if c is None or now_ms - c["made_ms"] >= CODE_LIFE_MS:
                c = self.codes[device_id] = {"code": self._new_code(), "prev": c["code"] if c else None, "made_ms": now_ms}
            return c["code"]

    def _rotate(self, device_id: str, now_ms: int) -> None:
        """After a sign-in or sign-out the QR changes, and the old one stops working (a photo of it is useless)."""
        with self.lock:
            self.codes[device_id] = {"code": self._new_code(), "prev": None, "made_ms": now_ms}

    def code_ok(self, device_id: str, code: str, now_ms: int) -> bool:
        with self.lock:
            c = self.codes.get(device_id)
            return c is not None and isinstance(code, str) and code in (c["code"], c["prev"]) and now_ms - c["made_ms"] < 2 * CODE_LIFE_MS

    def signin_url(self, device_id: str, lane, now_ms: int) -> Optional[str]:
        dt = self.core.deeptens
        if dt is None or not dt.linked or lane is None or not dt.conf.get("monitor_id"):
            return None
        return f"{dt.conf['url']}/t/{dt.conf['monitor_id']}/{lane}/{self.code(device_id, now_ms)}/"

    # ------------------------------------------------------------------------------------------------------ views
    @staticmethod
    def _view(r) -> dict:
        return {"id": r["id"], "athlete_id": r["athlete_id"], "number": r["number"], "name": r["name"], "public_name": r["public_name"],
                "via": r["via"], "started_ms": r["started_ms"], "ended_ms": r["ended_ms"], "end_why": r["end_why"]}

    @staticmethod
    def public_view(v: Optional[dict]) -> Optional[dict]:
        """What anyone on the range network may see of a signed-in athlete: the name they chose for public pages."""
        if v is None:
            return None
        return {"public_name": v["public_name"], "via": v["via"], "started_ms": v["started_ms"]}

    def on(self, c, device_id: str) -> Optional[dict]:
        r = c.execute("SELECT * FROM athlete_sessions WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        return self._view(r) if r else None

    @staticmethod
    def name_on(c, device_id: str) -> Optional[str]:
        """The results name of the athlete signed in on a target: a match it joins without a typed name takes it."""
        r = c.execute("SELECT public_name FROM athlete_sessions WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        return r["public_name"] if r else None

    def open_sessions(self) -> list:
        return [self._view(r) for r in self.core.db.conn().execute("SELECT * FROM athlete_sessions WHERE ended_ms IS NULL ORDER BY id")]

    def roster_athletes(self) -> list:
        """The range's athletes from the roster (for staff signing someone in), without their PIN hashes."""
        dt = self.core.deeptens
        if dt is None or not dt.roster_fresh():
            return []
        return [{k: a.get(k) for k in ("athlete_id", "number", "name", "public_name")} for a in dt.roster.get("athletes", []) if isinstance(a, dict)]

    # --------------------------------------------------------------------------------------------- signing in/out
    @staticmethod
    def _athlete(a) -> dict:
        if not isinstance(a, dict) or not isinstance(a.get("athlete_id"), int) or isinstance(a.get("athlete_id"), bool):
            raise SigninError("deepTENS sent no athlete")
        name = str(a.get("name") or "").strip()[:80] or "Athlete"
        return {"athlete_id": a["athlete_id"], "number": str(a.get("number") or "")[:6] or None, "name": name,
                "public_name": str(a.get("public_name") or "").strip()[:60] or "Athlete"}

    def sign_in(self, device_id: str, athlete: dict, via: str, claim: Optional[dict] = None) -> dict:
        """Sign an athlete in at a target. A lane in use is refused (sign out first); the athlete's session on another
        lane ends. Outside a match a new series starts in the athlete's results name; in a match whose firing point has
        no name yet, the competitor becomes the athlete."""
        if via not in VIAS:
            raise ValueError(via)
        dt = self.core.deeptens
        if dt is None or not dt.linked:
            raise SigninError("this FeT Monitor is not linked to deepTENS")
        a = self._athlete(athlete)
        core = self.core
        now = core.now_ms()
        ended = []
        with core.db.write() as c:
            t = c.execute("SELECT lane FROM targets WHERE device_id=? AND removed=0 AND banned=0", (device_id,)).fetchone()
            if t is None:
                raise SigninError("unknown target")
            if t["lane"] is None:
                raise SigninError("this target has no lane: Range Control gives it one")
            cur = self.on(c, device_id)
            if cur is not None:
                if cur["athlete_id"] == a["athlete_id"]:
                    return cur
                raise SigninError(f"lane {t['lane']} is in use: sign out first")
            for other in c.execute("SELECT * FROM athlete_sessions WHERE athlete_id=? AND ended_ms IS NULL", (a["athlete_id"],)).fetchall():
                self._end(c, other, "elsewhere", now)
                ended.append(other["device_id"])
            if claim is not None:                        # a poster's claim, confirmed by a shot: theirs since the claim
                c.execute("UPDATE series SET shooter=? WHERE id=?", (a["public_name"], claim["series_id"]))
                c.execute("UPDATE segments SET shooter=? WHERE series_id=?", (a["public_name"], claim["series_id"]))
            elif core.matches.lock_of(c, device_id):
                c.execute("""UPDATE match_lanes SET competitor=? WHERE device_id=? AND competitor IS NULL AND left_ms IS NULL AND match_id IN
                             (SELECT id FROM matches WHERE state IN """ + ACTIVE_SQL + ")", (a["public_name"], device_id))
            else:
                seg = c.execute("SELECT mode FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
                core._mode_change(c, device_id, seg["mode"] if seg else "practice", a["public_name"], True, None, a["public_name"], now)
            sid = c.execute("INSERT INTO athlete_sessions (device_id, lane, athlete_id, number, name, public_name, via, started_ms, last_ms) "
                            "VALUES (?,?,?,?,?,?,?,?,?)", (device_id, t["lane"], a["athlete_id"], a["number"], a["name"], a["public_name"],
                                                         via, claim["made_ms"] if claim else now, now)).lastrowid
            out = self.on(c, device_id)
        self._rotate(device_id, now)
        if claim is None:                                # someone signed in at the lane: a claim waiting there gives way
            self._drop_claim(device_id, "cancelled", "someone signed in at the lane")
            for d, cl in list(self.claims.items()):    # and this athlete's own claim elsewhere is no longer needed
                if cl["athlete"]["athlete_id"] == a["athlete_id"]:
                    self._drop_claim(d, "cancelled", "signed in on another lane")
        for d in [device_id] + ended:
            core.hub.publish({"type": "mode", "device_id": d})
        dt.changed("s", sid)
        return out

    def sign_out(self, device_id: str, why: str = "signout", athlete_id: Optional[int] = None, staff: bool = False) -> Optional[dict]:
        """End the target's session (athlete_id: only if it is theirs). In a match only staff can (the lane's own
        screen and phones are refused: it ends after the match by the idle time)."""
        core = self.core
        now = core.now_ms()
        with core.db.write() as c:
            r = c.execute("SELECT * FROM athlete_sessions WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
            if r is None:
                return None
            if athlete_id is not None and r["athlete_id"] != athlete_id:
                raise SigninError("someone else is signed in on this lane")
            if not staff and core.matches.lock_of(c, device_id):
                raise SigninError("this lane is in a match: you are signed out after it")
            self._end(c, r, why, now)
            out = self._view(c.execute("SELECT * FROM athlete_sessions WHERE id=?", (r["id"],)).fetchone())
        self._rotate(device_id, now)
        core.hub.publish({"type": "mode", "device_id": device_id})
        if core.deeptens is not None:
            core.deeptens.changed("s", r["id"])
        return out

    def _end(self, c, r, why: str, now: int) -> None:
        """Inside a transaction: the session ends now; outside a match the lane starts a fresh series in no one's name."""
        from .core import RANGE_NAME, KIND_OF_MODE, session_name
        c.execute("UPDATE athlete_sessions SET ended_ms=?, end_why=? WHERE id=?", (now, why, r["id"]))
        if not self.core.matches.lock_of(c, r["device_id"]):
            seg = c.execute("SELECT mode FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (r["device_id"],)).fetchone()
            mode = seg["mode"] if seg else "practice"
            self.core._mode_change(c, r["device_id"], mode, session_name(RANGE_NAME, KIND_OF_MODE.get(mode, "match")), True, None, RANGE_NAME, now)

    def end_all(self, why: str) -> None:
        """Unlinked: nobody stays signed in (their sessions cannot reach deepTENS any more)."""
        now = self.core.now_ms()
        with self.core.db.write() as c:
            for r in c.execute("SELECT * FROM athlete_sessions WHERE ended_ms IS NULL").fetchall():
                self._end(c, r, why, now)
        self.codes.clear()
        self.claims.clear()

    # ---------------------------------------------------------------------------------------------------- the PIN
    def pin_sign_in(self, device_id: str, number, pin) -> dict:
        """The lane's screen: athlete number and PIN. deepTENS checks them while it can be reached (any athlete);
        otherwise the range's athletes in the roster (offline). Five wrong tries for a number lock it here for 15 min."""
        dt = self.core.deeptens
        if dt is None or not dt.linked:
            raise SigninError("this FeT Monitor is not linked to deepTENS")
        number = "".join(ch for ch in str(number or "") if ch.isdigit())
        if len(number) != 6 or not isinstance(pin, str) or not pin.isdigit() or not 4 <= len(pin) <= 6:
            raise SigninError("the 6-digit athlete number and the 4 to 6 digit PIN, please")
        now = self.core.now_ms()
        with self.lock:
            f = self.fails.get(number)
            if f and f[1] > now:
                raise SigninError("too many wrong PINs: try again in 15 minutes")
        try:
            st, r = dt._call("/athletes/pin", {"number": number, "pin": pin}, timeout=PIN_TIMEOUT_S)
        except OSError:
            st, r = None, {}
        if st == 200:
            self._fail(number, ok=True)
            return self.sign_in(device_id, r.get("athlete"), "pin")
        if st in (403, 429):
            if st == 403:
                self._fail(number)
            raise SigninError(r.get("error") or "wrong athlete number or PIN")
        if st == 401:
            raise SigninError("this FeT Monitor is not linked to deepTENS any more")
        # deepTENS cannot be reached (or is in trouble): the range's own athletes, from the roster
        if not dt.roster_fresh():
            raise SigninError("deepTENS cannot be reached and this Pi has no recent list of the range's athletes")
        for a in dt.roster.get("athletes", []):
            if isinstance(a, dict) and a.get("number") == number:
                if a.get("pin") and verify_password(pin, a["pin"]):
                    self._fail(number, ok=True)
                    return self.sign_in(device_id, a, "pin_offline")
                break
        self._fail(number)
        raise SigninError("wrong athlete number or PIN (deepTENS cannot be reached: only athletes on this range's list can sign in)")

    def _fail(self, number: str, ok: bool = False) -> None:
        with self.lock:
            if ok:
                self.fails.pop(number, None)
                return
            f = self.fails.setdefault(number, [0, 0])
            f[0] += 1
            if f[0] >= PIN_FAILS:
                self.fails[number] = [0, self.core.now_ms() + PIN_LOCK_MS]

    def staff_sign_in(self, device_id: str, athlete_id) -> dict:
        """Range staff sign one of the range's athletes in (from the roster)."""
        for a in self.roster_athletes():
            if a["athlete_id"] == athlete_id:
                return self.sign_in(device_id, a, "staff")
        raise SigninError("not one of this range's athletes (a Range Manager adds them on deepTENS)")

    # ------------------------------------------------------------------------------------------- phones (the link)
    def phone(self, s: dict) -> tuple:
        """A sign-in or sign-out from a phone that deepTENS passed on: (ok, why)."""
        lane = s.get("lane")
        c = self.core.db.conn()
        t = c.execute("SELECT device_id FROM targets WHERE lane=? AND removed=0 AND banned=0", (lane,)).fetchone() if isinstance(lane, int) else None
        if t is None:
            return False, f"lane {lane} is not on this FeT Monitor any more"
        if s.get("kind") == "claim":
            return self.claim(s)
        try:
            if s.get("kind") == "out":
                a = self._athlete(s.get("athlete"))
                if self.sign_out(t["device_id"], "signout", athlete_id=a["athlete_id"]) is None:
                    return False, "nobody is signed in on that lane"
                return True, ""
            if not self.code_ok(t["device_id"], s.get("code"), self.core.now_ms()):
                return False, "that code is old: scan the lane's code again"
            self.sign_in(t["device_id"], s.get("athlete"), "phone")
            return True, ""
        except SigninError as exc:
            return False, str(exc)

    # ------------------------------------------------------------------------------------- posters: claim, then a shot
    def lane_free(self, c, device_id: str, now: int) -> str:
        """Why a poster cannot claim this target now ('' = it can): nobody signed in, not in a match, no shot on it for 5
        minutes, reporting, and no other claim waiting."""
        t = c.execute("SELECT lane, last_seen_ms FROM targets WHERE device_id=? AND removed=0 AND banned=0", (device_id,)).fetchone()
        if t is None or t["lane"] is None:
            return "that lane is not on this FeT Monitor any more"
        if now - t["last_seen_ms"] > self.core.offline_after_ms:
            return "the target is not reporting: tell the range officer"
        if self.on(c, device_id) is not None:
            return "someone is signed in on that lane"
        if self.core.matches.lock_of(c, device_id):
            return "that lane is in a match: the match director assigns it"
        if device_id in self.claims:
            return "someone else is claiming that lane: try again in two minutes"
        last = c.execute("SELECT MAX(event_ms) FROM shots WHERE device_id=?", (device_id,)).fetchone()[0]
        if last is not None and now - last < CLAIM_QUIET_MS:
            return "someone shot on that lane in the last 5 minutes"
        return ""

    def claim(self, s: dict) -> tuple:
        """A poster's claim that deepTENS passed on: (ok, why). The lane waits 2 minutes for a shot; a fresh series in
        no one's name starts now, and takes the athlete's name when their shot confirms the claim."""
        from .core import RANGE_NAME, KIND_OF_MODE, session_name
        dt = self.core.deeptens
        if dt is None or not dt.linked:
            return False, "this FeT Monitor is not linked to deepTENS"
        lane = s.get("lane")
        core = self.core
        c = core.db.conn()
        t = c.execute("SELECT device_id FROM targets WHERE lane=? AND removed=0 AND banned=0", (lane,)).fetchone() if isinstance(lane, int) else None
        if t is None:
            return False, f"lane {lane} is not on this FeT Monitor any more"
        try:
            a = self._athlete(s.get("athlete"))
        except SigninError as exc:
            return False, str(exc)
        did, now = t["device_id"], core.now_ms()
        for d, cl in list(self.claims.items()):        # one claim per athlete: a new one replaces theirs
            if cl["athlete"]["athlete_id"] == a["athlete_id"]:
                self._drop_claim(d, "cancelled", "you claimed another lane")
        with core.db.write() as w:
            why = self.lane_free(w, did, now)
            if why:
                return False, why
            seg = w.execute("SELECT mode FROM segments WHERE device_id=? AND ended_ms IS NULL ORDER BY id DESC LIMIT 1", (did,)).fetchone()
            mode = seg["mode"] if seg else "practice"
            series_id = core._mode_change(w, did, mode, session_name(RANGE_NAME, KIND_OF_MODE.get(mode, "match")), True, None, RANGE_NAME, now)[0]
            self.claims[did] = {"id": s.get("id"), "athlete": a, "made_ms": now, "until_ms": now + CLAIM_MS, "series_id": series_id, "lane": lane}
        core.hub.publish({"type": "mode", "device_id": did})
        return True, ""

    def claim_view(self, device_id: str) -> Optional[dict]:
        """What the lane's screen shows of a claim waiting there: the athlete's public name and until when."""
        cl = self.claims.get(device_id)
        return {"public_name": cl["athlete"]["public_name"], "until_ms": cl["until_ms"], "left_ms": max(0, cl["until_ms"] - self.core.now_ms())} if cl else None

    def _drop_claim(self, device_id: str, state: str, why: str) -> None:
        cl = self.claims.pop(device_id, None)
        if cl is not None:
            self.claim_news.append({"id": cl["id"], "ok": state == "confirmed", "state": state, "why": why})
            self.core.hub.publish({"type": "mode", "device_id": device_id})

    def cancel_claim(self, device_id: str) -> bool:
        """'Not you? Cancel' on the lane's screen (anyone standing at the lane)."""
        if device_id not in self.claims:
            return False
        self._drop_claim(device_id, "cancelled", "cancelled at the lane")
        return True

    def claims_check(self, device_id: Optional[str] = None) -> None:
        """After shots arrive (one target) and every few seconds (all): the first shot fired on the lane after the claim
        and within its 2 minutes confirms it (also when it arrives up to 30 s late); without one it lapses."""
        now = self.core.now_ms()
        c = self.core.db.conn()
        for did, cl in list(self.claims.items()):
            if device_id is not None and did != device_id:
                continue
            hit = c.execute("SELECT 1 FROM shots WHERE device_id=? AND event_ms>=? AND event_ms<=? LIMIT 1", (did, cl["made_ms"], cl["until_ms"])).fetchone()
            if hit:
                self.claims.pop(did, None)
                try:
                    self.sign_in(did, cl["athlete"], "poster", claim=cl)
                except SigninError as exc:
                    self.claim_news.append({"id": cl["id"], "ok": False, "state": "cancelled", "why": str(exc)})
                else:
                    self.claim_news.append({"id": cl["id"], "ok": True, "state": "confirmed", "why": ""})
            elif now > cl["until_ms"] + CLAIM_GRACE_MS:
                self._drop_claim(did, "lapsed", "no shot on the lane within 2 minutes")

    def take_claim_news(self) -> list:
        out, self.claim_news = self.claim_news, []
        return out

    # ------------------------------------------------------------------------------------------- timeouts and sending
    def idle_ms(self) -> int:
        dt = self.core.deeptens
        n = dt.roster.get("signin_idle_min") if dt is not None else None
        return (n if isinstance(n, int) and 5 <= n <= 240 else IDLE_MIN_DEFAULT) * 60000

    def tick(self) -> None:
        """Every few seconds (the link's thread): sign out the idle (no shot for the range's idle time) and those past
        4 hours, never during a match (its time does not count as idle); queue a new snapshot of each session whose
        shots changed, also late shots after it ended."""
        core = self.core
        self.claims_check()
        now = core.now_ms()
        idle = self.idle_ms()
        c = core.db.conn()
        for r in c.execute("SELECT * FROM athlete_sessions WHERE ended_ms IS NULL").fetchall():
            if core.matches.lock_of(c, r["device_id"]):
                with core.db.write() as w:
                    w.execute("UPDATE athlete_sessions SET last_ms=? WHERE id=?", (now, r["id"]))
                continue
            last = c.execute("SELECT MAX(event_ms) FROM shots WHERE device_id=? AND event_ms>=?", (r["device_id"], r["started_ms"])).fetchone()[0]
            since = max(r["last_ms"], last or 0)
            why = "idle" if now - since >= idle else "limit" if now - r["started_ms"] >= LIMIT_MS else None
            if why:
                try:
                    self.sign_out(r["device_id"], why, staff=True)
                except SigninError:
                    pass
        dt = core.deeptens
        if dt is None or not dt.linked:
            return
        for r in c.execute("SELECT * FROM athlete_sessions WHERE ended_ms IS NULL OR ended_ms>?", (now - LATE_MS,)).fetchall():
            end = r["ended_ms"] if r["ended_ms"] is not None else 1 << 62
            s = c.execute("SELECT COUNT(*), MAX(id) FROM shots WHERE device_id=? AND event_ms>=? AND event_ms<?", (r["device_id"], r["started_ms"], end)).fetchone()
            sig = (s[0], s[1], r["ended_ms"])
            if self.sent.get(r["id"]) != sig:
                self.sent[r["id"]] = sig
                dt.changed("s", r["id"])

    def snapshot(self, n: int) -> Optional[tuple]:
        """("session", "s<n>", data): the session and every series it touched, with the shots fired in it."""
        c = self.core.db.conn()
        r = c.execute("SELECT * FROM athlete_sessions WHERE id=?", (n,)).fetchone()
        if r is None:
            return None
        end = r["ended_ms"] if r["ended_ms"] is not None else 1 << 62
        tname = c.execute("SELECT name FROM targets WHERE device_id=?", (r["device_id"],)).fetchone()
        series = []
        for ser in c.execute("""SELECT * FROM series WHERE id IN (SELECT DISTINCT series_id FROM shots WHERE device_id=? AND event_ms>=? AND event_ms<?
                                AND series_id IS NOT NULL) ORDER BY started_ms, id""", (r["device_id"], r["started_ms"], end)).fetchall():
            ml = c.execute("SELECT m.id, m.name, m.competition_id FROM match_lanes l JOIN matches m ON m.id=l.match_id WHERE l.series_id=? LIMIT 1",
                           (ser["id"],)).fetchone()
            item = {"series_id": ser["id"], "kind": ser["kind"], "name": ser["shooter"], "label": ser["label"], "started_ms": ser["started_ms"],
                    "ended_ms": ser["ended_ms"], "face": F.view(F.face(ser["face_id"])),
                    "match": {"key": f"c{ml['competition_id']}" if ml["competition_id"] else f"m{ml['id']}", "name": ml["name"]} if ml else None}
            for stream in ("scored", "sighter"):
                rows = c.execute("SELECT * FROM shots WHERE series_id=? AND stream=? AND event_ms>=? AND event_ms<? ORDER BY series_no, id",
                                 (ser["id"], stream, r["started_ms"], end)).fetchall()
                v = self.core._stream_view_rows(c, ser, stream, rows)
                item[stream] = {"agg": v["agg"], "groups": v["groups"], "shots": [dict(self.core._brief(x), t=x["event_ms"]) for x in rows]}
            series.append(item)
        data = {"athlete_id": r["athlete_id"], "number": r["number"], "lane": r["lane"], "target": tname["name"] if tname else None,
                "via": r["via"], "started_ms": r["started_ms"], "ended_ms": r["ended_ms"], "end_why": r["end_why"], "series": series}
        return "session", f"s{n}", data

    # ------------------------------------------------------------------------------------------------ match results
    def athlete_of_series(self, c, series_id) -> Optional[int]:
        """The athlete signed in on the series' target when its first shot was fired (or when it started)."""
        ser = c.execute("SELECT device_id, started_ms FROM series WHERE id=?", (series_id,)).fetchone() if series_id is not None else None
        if ser is None:
            return None
        first = c.execute("SELECT MIN(event_ms) FROM shots WHERE series_id=?", (series_id,)).fetchone()[0]
        at = first if first is not None else ser["started_ms"]
        r = c.execute("""SELECT athlete_id FROM athlete_sessions WHERE device_id=? AND started_ms<=? AND (ended_ms IS NULL OR ended_ms>?)
                         ORDER BY id DESC LIMIT 1""", (ser["device_id"], at, at)).fetchone()
        return r["athlete_id"] if r else None

    def tag(self, res: dict, kind: str, n: int) -> dict:
        """Results for deepTENS with each competitor's athlete (who was signed in on their firing point), if any."""
        c = self.core.db.conn()
        for cl in res.get("classes", []):
            for e in cl.get("individuals", []):
                if kind == "c":
                    rows = c.execute("SELECT l.series_id FROM match_lanes l JOIN matches m ON m.id=l.match_id WHERE m.competition_id=? AND l.competitor_id=?",
                                     (n, e.get("id"))).fetchall()
                else:
                    rows = c.execute("SELECT series_id FROM match_lanes WHERE match_id=? AND lane=?", (n, e.get("id"))).fetchall()
                for row in rows:
                    aid = self.athlete_of_series(c, row["series_id"])
                    if aid is not None:
                        e["athlete_id"] = aid
                        break
        return res
