"""Lane displays: a CYD shot monitor at a firing point, paired with one target by Range Control (docs/PLAN-cyd.md).

Pairing. The display asks for a code (pair): it gets a 6-character code to show and a secret token it keeps. Range
Control types the code and chooses the target (approve): from then on the token is the display's key. A display that
asks before that is told "pending"; one whose token is unknown (removed, replaced, expired) is told "none" and pairs
again. Codes live in memory only (the public pairing request never writes to the database), at most MAX_PENDING at a
time, for PAIR_LIFE_MS. One display per target, one target per display: a new display on a target replaces the old.

The view. What the display draws, for its target: the target, who is signed in (the public name only) or the sign-in
QR address, a poster's claim, the mode, the face, the current bull's shots and the totals with scores already written
as the screens write them, the match clock and the shooter's own buttons. Nothing is scored here: it comes from the
same place the phones' screens do (Core.target_entry). rev changes whenever what is drawn changes (not with clocks
counting down: the display counts them itself from ends_at_ms and server_time_ms), so a display asks with the rev it
has and waits up to MAX_WAIT_S for a change (long poll): a shot reaches it as soon as the server has it.

Actions, from the display's own buttons: sign in with athlete number and PIN, sign out, "Not you? Cancel" a claim,
Start record and Back to sighters. Each goes through the same rules as the lane's screen on a phone. Holding the
target's display token stands for being at the firing point (the phone needs the lane's control claim instead).
"""
from __future__ import annotations

import hashlib
import json
import queue
import random
import re
import secrets
import threading
import time
from typing import Callable, Dict, Optional

from fetcore import faces as F

from .athletes import SigninError
from .displays import ALPHABET
from .matches import MatchError

PAIR_LIFE_MS = 10 * 60 * 1000
MAX_PENDING = 20
CODE_LEN = 6
MAX_WAIT_S = 30
HW_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
FW_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,20}$")
NAME_MAX = 40
ACTIONS = ("signin", "signout", "claim_cancel", "start_record", "back_to_sighters", "control_here")
VOLATILE = ("server_time_ms", "left_ms", "remaining_ms")       # change by themselves: not part of rev


class DisplayError(ValueError):
    pass


class NotAssigned(PermissionError):
    """The token is not (or no longer) a display's: pairing says whether it is still waiting for Range Control."""
    def __init__(self, pairing: str):
        super().__init__("not assigned")
        self.pairing = pairing


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def norm_code(code) -> str:
    """A pairing code as typed (any case, spaces or a dash allowed), or DisplayError."""
    s = "".join(ch for ch in str(code or "") if ch not in " -").upper()
    if len(s) != CODE_LEN or any(ch not in ALPHABET for ch in s):
        raise DisplayError("the code the display shows: %d letters and digits" % CODE_LEN)
    return s


def score_text(s: Optional[dict], whole: bool) -> str:
    """A shot's score as the screens write it (the dashboard's scoreText): M, whole rings, or the decimal."""
    if not s:
        return ""
    if s["miss"]:
        return "M"
    return str(s["ring"]) if whole else "%.1f" % s["decimal"]


def match_buttons(info: Optional[dict]) -> list:
    """The shooter's own buttons in a match, by the rule the lane's screen on a phone uses (dashboard matchBanner)."""
    if not info or info.get("state") != "running":
        return []
    out = []
    if (info.get("kind") == "stage" and not info.get("in_record")) or info.get("sighting") or info.get("can_record"):
        out.append("start_record")
    if (info.get("kind") == "stage" and info.get("in_record") and info.get("sighters_any_time")) or info.get("can_back"):
        out.append("back_to_sighters")
    return out


def _stable(v):
    if isinstance(v, dict):
        return {k: _stable(x) for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [_stable(x) for x in v]
    return v


class LaneDisplays:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.lock = threading.Lock()
        self.pending: Dict[str, dict] = {}          # code -> {"hash", "hw_id", "fw", "expires_ms"}
        self.seen: Dict[int, dict] = {}             # display id -> {"ms", "fw", "ip"} (memory only)

    def _pub(self):
        self.core.hub.publish({"type": "lane_display"})

    # ------------------------------------------------------------------------------------------------- pairing
    def pair(self, hw_id, fw=None) -> dict:
        """A display asks to be paired: {"code" to show, "token" to keep, "expires_ms"}. The same display asking
        again gets a new code (its old one stops working)."""
        if not isinstance(hw_id, str) or not HW_ID_RE.match(hw_id):
            raise DisplayError("hw_id: up to 40 letters, digits, '.', '_' or '-'")
        if fw is not None and (not isinstance(fw, str) or not FW_RE.match(fw)):
            raise DisplayError("fw: a version, up to 20 characters")
        now = self.core.now_ms()
        rnd = random.SystemRandom()
        with self.lock:
            for code in [k for k, p in self.pending.items() if p["expires_ms"] <= now or p["hw_id"] == hw_id]:
                del self.pending[code]
            if len(self.pending) >= MAX_PENDING:
                raise DisplayError("too many displays waiting to be paired: try again in a few minutes")
            while True:
                code = "".join(rnd.choice(ALPHABET) for _ in range(CODE_LEN))
                if code not in self.pending:
                    break
            token = "fetd_" + secrets.token_urlsafe(24)
            self.pending[code] = {"hash": _hash(token), "hw_id": hw_id, "fw": fw, "expires_ms": now + PAIR_LIFE_MS}
        return {"code": code, "token": token, "expires_ms": now + PAIR_LIFE_MS, "expires_in_s": PAIR_LIFE_MS // 1000}

    def approve(self, code, device_id, name=None) -> dict:
        """Range Control: the display showing this code shows this target from now on. A display already on that
        target is replaced, and this display leaves any other target."""
        code = norm_code(code)
        did = self._target(device_id)
        nm = self._name(name)
        now = self.core.now_ms()
        with self.lock:
            p = self.pending.get(code)
            if p is None or p["expires_ms"] <= now:
                self.pending.pop(code, None)
                raise DisplayError("no display is showing that code (codes last 10 minutes): check it, or restart the display")
            del self.pending[code]
        with self.db.write() as c:
            c.execute("DELETE FROM lane_displays WHERE device_id=? OR hw_id=?", (did, p["hw_id"]))
            cur = c.execute("INSERT INTO lane_displays (name, hw_id, device_id, token_hash, created_ms, updated_ms) VALUES (?,?,?,?,?,?)",
                            (nm or "Display " + p["hw_id"][-6:], p["hw_id"], did, p["hash"], now, now))
            new_id = cur.lastrowid
        if p.get("fw"):
            self.seen[new_id] = {"ms": now, "fw": p["fw"], "ip": None}
        self._pub()
        return self.get(new_id)

    def program(self, hw_id, device_id, name=None) -> tuple:
        """The flash station programmed a CYD over USB (flashstation.py): it shows this target from now on, with no code
        to type. (the display's record, its token: the station writes it into the display). As approve(): a display
        already on that target is replaced, and this display leaves any other target; a code it showed is void."""
        if not isinstance(hw_id, str) or not HW_ID_RE.match(hw_id):
            raise DisplayError("hw_id: up to 40 letters, digits, '.', '_' or '-'")
        did = self._target(device_id)
        nm = self._name(name)
        now = self.core.now_ms()
        token = "fetd_" + secrets.token_urlsafe(24)
        with self.lock:
            for code in [k for k, p in self.pending.items() if p["hw_id"] == hw_id]:
                del self.pending[code]
        with self.db.write() as c:
            c.execute("DELETE FROM lane_displays WHERE device_id=? OR hw_id=?", (did, hw_id))
            cur = c.execute("INSERT INTO lane_displays (name, hw_id, device_id, token_hash, created_ms, updated_ms) VALUES (?,?,?,?,?,?)",
                            (nm or "Display " + hw_id[-6:], hw_id, did, _hash(token), now, now))
            new_id = cur.lastrowid
        self._pub()
        return self.get(new_id), token

    def change(self, display_id: int, device_id=None, name=None) -> dict:
        """Range Control: another target (the one there now is replaced) or another name."""
        did = self._target(device_id) if device_id is not None else None
        nm = self._name(name) if name is not None else None
        with self.db.write() as c:
            r = c.execute("SELECT * FROM lane_displays WHERE id=?", (display_id,)).fetchone()
            if r is None:
                raise DisplayError("no such display")
            if did is not None and did != r["device_id"]:
                c.execute("DELETE FROM lane_displays WHERE device_id=? AND id!=?", (did, display_id))
                c.execute("UPDATE lane_displays SET device_id=?, updated_ms=? WHERE id=?", (did, self.core.now_ms(), display_id))
            if nm:
                c.execute("UPDATE lane_displays SET name=?, updated_ms=? WHERE id=?", (nm, self.core.now_ms(), display_id))
        self._pub()
        return self.get(display_id)

    def remove(self, display_id: int) -> None:
        with self.db.write() as c:
            if not c.execute("DELETE FROM lane_displays WHERE id=?", (display_id,)).rowcount:
                raise DisplayError("no such display")
        self.seen.pop(display_id, None)
        self._pub()

    def _target(self, device_id) -> str:
        if not isinstance(device_id, str) or not self.db.conn().execute(
                "SELECT 1 FROM targets WHERE device_id=? AND removed=0 AND banned=0", (device_id,)).fetchone():
            raise DisplayError("choose one of the range's targets")
        return device_id

    @staticmethod
    def _name(name) -> Optional[str]:
        if name is None or (isinstance(name, str) and not name.strip()):
            return None
        if not isinstance(name, str) or len(name.strip()) > NAME_MAX:
            raise DisplayError("a display's name: up to %d characters" % NAME_MAX)
        return name.strip()

    # ------------------------------------------------------------------------------------------- Range Control
    def get(self, display_id: int) -> dict:
        r = self.db.conn().execute("SELECT * FROM lane_displays WHERE id=?", (display_id,)).fetchone()
        if r is None:
            raise DisplayError("no such display")
        return self._row_view(r)

    def _row_view(self, r) -> dict:
        t = self.db.conn().execute("SELECT name, lane FROM targets WHERE device_id=?", (r["device_id"],)).fetchone()
        s = self.seen.get(r["id"], {})
        now = self.core.now_ms()
        return {"id": r["id"], "name": r["name"], "hw_id": r["hw_id"], "device_id": r["device_id"],
                "target_name": (t["name"] if t and t["name"] else r["device_id"]), "lane": t["lane"] if t else None,
                "last_seen_ms": s.get("ms"), "online": bool(s.get("ms")) and now - s["ms"] <= (MAX_WAIT_S + 30) * 1000,
                "fw": s.get("fw"), "ip": s.get("ip"), "created_ms": r["created_ms"]}

    def list(self) -> dict:
        now = self.core.now_ms()
        with self.lock:
            waiting = sum(1 for p in self.pending.values() if p["expires_ms"] > now)
        rows = self.db.conn().execute("SELECT * FROM lane_displays ORDER BY name COLLATE NOCASE, id").fetchall()
        return {"displays": [self._row_view(r) for r in rows], "waiting": waiting}

    # ---------------------------------------------------------------------------------------------- the display
    def auth(self, token, fw=None, ip=None):
        """The display's row for a token, or NotAssigned ("pending": Range Control has not typed its code yet)."""
        if not isinstance(token, str) or not token.startswith("fetd_") or len(token) > 80:
            raise NotAssigned("none")
        h = _hash(token)
        r = self.db.conn().execute("SELECT * FROM lane_displays WHERE token_hash=?", (h,)).fetchone()
        now = self.core.now_ms()
        if r is None:
            with self.lock:
                waiting = any(p["hash"] == h and p["expires_ms"] > now for p in self.pending.values())
            raise NotAssigned("pending" if waiting else "none")
        prev = self.seen.get(r["id"], {})
        self.seen[r["id"]] = {"ms": now, "fw": fw if isinstance(fw, str) and FW_RE.match(fw) else prev.get("fw"), "ip": ip}
        return r

    @staticmethod
    def _sights(st):
        """Sights for the display (user 2026-11-22): the group (shots after the mark), its centre, and the clicks only
        when the range master shows them; never rings."""
        if st is None:
            return None
        return {"show": st["show"], "ready": st["ready"], "min_shots": st["min_shots"], "shots": st["points"],
                "misses": st["misses"], "center_h": st.get("center_h"), "clicks": st["clicks"]}

    def view(self, r) -> dict:
        """What the display draws now (see the module notes)."""
        core = self.core
        now = core.now_ms()
        c = self.db.conn()
        t_row = c.execute("""SELECT t.*, (SELECT COUNT(*) FROM shots s WHERE s.device_id=t.device_id) AS shot_count
                             FROM targets t WHERE t.device_id=?""", (r["device_id"],)).fetchone()
        if t_row is None or t_row["removed"] or t_row["banned"]:
            raise NotAssigned("none")                    # its target was removed: Range Control assigns it again
        t = core.target_entry(c, t_row, now)
        v = t["view"]
        face = v["face"]
        whole = face.get("decimal") is False
        agg = v["agg"]
        shots = [{"n": s["n"], "miss": s["miss"], "inner": s["inner"], "score": score_text(s, whole),
                  "x_h": None if s["miss"] else int(round(s["x_mm"] * 100)), "y_h": None if s["miss"] else int(round(s["y_mm"] * 100))}
                 for s in v["bull_shots"]]
        last = v.get("last_shot")
        info = t.get("match")
        match = None
        if info:
            match = {k: info.get(k) for k in ("match_id", "name", "competitor", "fp", "label", "state", "kind", "remaining_ms",
                                               "ends_at_ms", "next", "in_record", "sighting", "position")}
            match["buttons"] = match_buttons(info)
        dt = core.deeptens
        out = {
            "v": 1,
            "display": {"id": r["id"], "name": r["name"]},
            "target": {"device_id": t["device_id"], "name": t["name"], "lane": t["lane"], "online": t["online"]},
            "athlete": t["athlete"],                     # public: the name they chose for public pages
            "signin_url": t["signin_url"],
            "pin_signin": t["athlete"] is None and dt is not None and bool(dt.linked),
            "claim": t["claim"],
            "can_sign_out": t["athlete"] is not None and info is None,
            "control": core.athletes.control_of(c, r["device_id"]),     # "phone": CONTROL HERE (user 2026-11-22)
            "mode": t["mode"], "kind": v["kind"], "stream": v["stream"], "label": v["label"],
            "face": {k: face.get(k) for k in ("id", "name", "decimal", "rings", "black_mm", "white_ten", "inner_mm", "projectile_mm", "ring_mm")},
            "shots": shots, "count": v["count"], "bull": v["bull_index"] + 1, "bulls": v["bull_count"],
            "last": None if not last else {"n": last["n"], "score": score_text(last, whole), "inner": last["inner"], "miss": last["miss"]},
            "totals": {"shots": agg["shots"], "score": str(agg["integer"]) if whole else "%.1f" % agg["decimal"],
                       "x": agg["x"], "misses": agg["misses"]},
            "match": match,
            "sights": self._sights(core.sights_state(c, r["device_id"], shots=True)),
            "server_time_ms": now,
        }
        out["rev"] = hashlib.sha256(json.dumps(_stable(out), sort_keys=True).encode()).hexdigest()[:16]
        return out

    def wait_view(self, token, rev=None, wait_s: float = 0, fw=None, ip=None, stopping: Callable[[], bool] = lambda: False) -> dict:
        """The view, as soon as its rev differs from the one given, or after wait_s seconds (at most MAX_WAIT_S)."""
        wait_s = max(0.0, min(float(wait_s or 0), MAX_WAIT_S))
        q = self.core.hub.subscribe() if wait_s and rev else None
        try:
            deadline = time.monotonic() + wait_s
            while True:
                out = self.view(self.auth(token, fw, ip))
                left = deadline - time.monotonic()
                if q is None or out["rev"] != rev or left <= 0 or stopping():
                    return out
                try:
                    q.get(timeout=min(left, 1.0))       # a change somewhere on the range, or a second passed
                    while True:                          # take every hint that came at once: one look is enough
                        q.get_nowait()
                except queue.Empty:
                    pass
        finally:
            if q is not None:
                self.core.hub.unsubscribe(q)

    def act(self, token, payload: dict, ip=None) -> dict:
        """One of the display's buttons; then the view."""
        r = self.auth(token, ip=ip)
        did = r["device_id"]
        action = payload.get("action") if isinstance(payload, dict) else None
        if action not in ACTIONS:
            raise DisplayError("action: one of " + ", ".join(ACTIONS))
        core = self.core
        if action == "signin":
            core.athletes.pin_sign_in(did, payload.get("number"), payload.get("pin"))       # SigninError: wrong, locked, offline
        elif action == "signout":
            if core.athletes.sign_out(did) is None:
                raise DisplayError("nobody is signed in here")
        elif action == "claim_cancel":
            if not core.athletes.cancel_claim(did):
                raise DisplayError("no claim is waiting here")
        elif action == "control_here":                 # the lane display is part of the lane: control comes back to it
            a = core.athletes.on(self.db.conn(), did)
            if a is None or core.athletes.control_of(self.db.conn(), did) != "phone":
                raise DisplayError("the controls are already here")
            core.athletes.set_control(did, a["athlete_id"], "lane")
        else:
            info = core.matches.target_info(self.db.conn(), did, core.now_ms())
            if action not in match_buttons(info):
                raise DisplayError("not now" if info else "this target is not in a match")
            core.matches.command(info["match_id"], action, "display " + r["name"], device_id=did)
        return self.view(self.auth(token, ip=ip))


__all__ = ["LaneDisplays", "DisplayError", "NotAssigned", "SigninError", "MatchError", "match_buttons", "score_text", "norm_code"]
