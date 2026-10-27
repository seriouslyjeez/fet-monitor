"""Match Control: runs a match's clock over a set of targets (lanes), switches the targets between SIGHTER and RECORD
as the format says, and keeps a timeline from which out-of-time shots are worked out.

The server owns the clock. A phase's time left = its length + extra time - time already used; time only runs while the
match is running (not paused). tick() (called a few times a second) ends phases whose time is up and starts the next.
Every change is written to match_log, so the flags are always recomputed from the same facts:
  phase_start / phase_end / pause / resume / go / restart / extra / start_record / abort.

Pacing: 'spoken' (default) runs phase after phase and the RO console speaks every command; 'officer' HOLDS at the
start of every phase until the range officer, having read the command shown, presses Next (command 'go'); 'auto' runs
phase after phase with no commands at all. Restarts (director): the
match goes back to a phase; shots fired in the parts that were restarted stay, flagged for a decision.

Shots are never dropped or changed here: a shot that the timeline says was out of time (or one record shot too many)
is FLAGGED, and does not count until the match director decides (count / annul / miss / -2 points).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from . import matchformats as mf
from .admin import ROLES, USERNAME_RE, MIN_PASSWORD, hash_password

ACTIVE = ("ready", "running", "paused", "holding")
LIVE = ("running", "paused", "holding")
PACING = ("spoken", "officer", "auto")   # spoken: the console speaks every command (default); officer: the range officer
# reads them and the clock waits for Next at every phase; auto: phases follow each other with no commands at all
DECISIONS = ("count", "annul", "miss", "penalty2")                 # for a flagged RECORD shot
SIGHTER_DECISIONS = ("ignore", "penalty2_first", "miss_first")    # for a flagged SIGHTING shot: it never becomes a record
# shot, but rules punish some of them on the first record shot (e.g. ISSF 6.11.1.1 h, k)
MAX_LANES = 100


class MatchError(ValueError):
    pass


class Matches:
    def __init__(self, core):
        self.core = core
        self.db = core.db

    def _pub(self, match_id):
        self.core.hub.publish({"type": "match", "match_id": match_id})

    # ------------------------------------------------------------------------------------------- users
    def users(self) -> List[dict]:
        return [{"username": r["username"], "role": r["role"]} for r in
                self.db.conn().execute("SELECT username, role FROM match_users ORDER BY username")]

    def user(self, username: str) -> Optional[dict]:
        r = self.db.conn().execute("SELECT role, pw_json FROM match_users WHERE username=?", (username,)).fetchone()
        return {"role": r["role"], "pw": json.loads(r["pw_json"])} if r else None

    def set_user(self, username, role, password=None) -> None:
        """Add a user, or change an existing user's role and/or password (password None keeps it)."""
        if not isinstance(username, str) or not USERNAME_RE.match(username) or username == "admin":
            raise MatchError("username: 2-32 letters, digits, . _ - (not 'admin')")
        if role not in ROLES:
            raise MatchError("role must be director or officer")
        existing = self.user(username)
        if password is None and existing is None:
            raise MatchError("a new user needs a password")
        if password is not None and (not isinstance(password, str) or len(password) < MIN_PASSWORD):
            raise MatchError(f"the password must be at least {MIN_PASSWORD} characters")
        pw = json.dumps(hash_password(password)) if password is not None else json.dumps(existing["pw"])
        with self.db.write() as c:
            c.execute("INSERT INTO match_users (username, role, pw_json, created_ms) VALUES (?,?,?,?) "
                      "ON CONFLICT(username) DO UPDATE SET role=excluded.role, pw_json=excluded.pw_json",
                      (username, role, pw, self.core.now_ms()))

    def remove_user(self, username: str) -> bool:
        with self.db.write() as c:
            return c.execute("DELETE FROM match_users WHERE username=?", (username,)).rowcount == 1

    # ------------------------------------------------------------------------------------------- formats
    def formats(self) -> List[dict]:
        out = [dict(f, builtin=True) for f in mf.builtin_list()]
        for r in self.db.conn().execute("SELECT json FROM match_formats ORDER BY id"):
            out.append(dict(json.loads(r["json"]), builtin=False))
        return out

    def format(self, fid: str) -> Optional[dict]:
        if fid in mf.BUILTIN:
            return mf.validate(mf.BUILTIN[fid])
        r = self.db.conn().execute("SELECT json FROM match_formats WHERE id=?", (fid,)).fetchone()
        return json.loads(r["json"]) if r else None

    def save_format(self, fmt, by: str) -> dict:
        try:
            clean = mf.validate(dict(fmt or {}, rulebook="Custom"))
        except ValueError as exc:
            raise MatchError(str(exc))
        if not clean["id"]:
            base = "custom-" + "".join(ch if ch.isalnum() else "-" for ch in clean["name"].lower()).strip("-")[:30]
            clean["id"] = base.rstrip("-") or "custom"
        if not clean["id"].startswith("custom-"):
            clean["id"] = ("custom-" + clean["id"])[:41]
        if clean["id"] in mf.BUILTIN:
            raise MatchError("that id belongs to a standard format")
        with self.db.write() as c:
            c.execute("INSERT INTO match_formats (id, json, created_by, updated_ms) VALUES (?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET json=excluded.json, updated_ms=excluded.updated_ms",
                      (clean["id"], json.dumps(clean), by, self.core.now_ms()))
        return clean

    def delete_format(self, fid: str) -> bool:
        with self.db.write() as c:
            return c.execute("DELETE FROM match_formats WHERE id=?", (fid,)).rowcount == 1

    # ------------------------------------------------------------------------------------------- creating
    def lock_of(self, c, device_id: str) -> Optional[str]:
        """The name of the active match a target belongs to, if any (such a target is locked against other changes)."""
        r = c.execute("""SELECT m.name FROM matches m JOIN match_lanes l ON l.match_id=m.id
                         WHERE l.device_id=? AND m.state IN ('ready','running','paused','holding')""", (device_id,)).fetchone()
        return r["name"] if r else None

    def create(self, name, fmt_id, lanes, by: str, override: Optional[dict] = None, paced: str = "spoken") -> int:
        """A match in state 'ready'. lanes: [{"device_id", "competitor"}] in firing-point order. override: a full format
        (e.g. the standard one with a longer first sighting period, CMP 5.3.1 d); it is marked as modified."""
        if not isinstance(name, str) or not name.strip():
            raise MatchError("the match needs a name")
        base = self.format(fmt_id) if isinstance(fmt_id, str) else None
        if base is None:
            raise MatchError("unknown format")
        fmt = base
        if override is not None:
            try:
                fmt = mf.validate(dict(override, id=base["id"], rulebook=base["rulebook"], edition=base.get("edition", "")))
            except ValueError as exc:
                raise MatchError(str(exc))
            if [(p["kind"], p["seconds"], p["shots"]) for p in fmt["phases"]] != [(p["kind"], p["seconds"], p["shots"]) for p in base["phases"]]:
                fmt["modified"] = True
        if paced not in PACING:
            raise MatchError("commands: spoken, officer (read by the range officer) or auto (no commands)")
        if not isinstance(lanes, list) or not 1 <= len(lanes) <= MAX_LANES:
            raise MatchError(f"choose 1 to {MAX_LANES} targets")
        seen = set()
        now = self.core.now_ms()
        with self.db.write() as c:
            for ln in lanes:
                did = ln.get("device_id") if isinstance(ln, dict) else None
                if not isinstance(did, str) or did in seen:
                    raise MatchError("each target once")
                seen.add(did)
                if c.execute("SELECT 1 FROM targets WHERE device_id=? AND removed=0 AND banned=0", (did,)).fetchone() is None:
                    raise MatchError(f"unknown target {did}")
                other = self.lock_of(c, did)
                if other:
                    raise MatchError(f"{did} is already in the match '{other}'")
            mid = c.execute("INSERT INTO matches (name, format_json, state, created_ms, created_by, paced) VALUES (?,?,?,?,?,?)",
                            (name.strip()[:80], json.dumps(fmt), "ready", now, by, paced)).lastrowid
            for i, ln in enumerate(lanes, 1):
                comp = ln.get("competitor")
                comp = comp.strip()[:60] if isinstance(comp, str) and comp.strip() else None
                c.execute("INSERT INTO match_lanes (match_id, device_id, lane, competitor) VALUES (?,?,?,?)",
                          (mid, ln["device_id"], i, comp))
            self._log(c, mid, now, "created", None, None, {"by": by})
        self._pub(mid)
        return mid

    # ------------------------------------------------------------------------------------------- running
    @staticmethod
    def _log(c, mid, at, kind, phase, device_id, data=None):
        c.execute("INSERT INTO match_log (match_id, at_ms, kind, phase, device_id, data) VALUES (?,?,?,?,?,?)",
                  (mid, at, kind, phase, device_id, json.dumps(data) if data is not None else None))

    def _row(self, c, mid):
        m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        if m is None:
            raise MatchError("unknown match")
        return m

    def _lanes(self, c, mid):
        return c.execute("SELECT * FROM match_lanes WHERE match_id=? ORDER BY lane", (mid,)).fetchall()

    def _session(self, m, ln) -> str:
        return ln["competitor"] or m["name"]

    def _set_modes(self, c, m, mode: str, now: int, only: Optional[str] = None) -> None:
        for ln in self._lanes(c, m["id"]):
            if only and ln["device_id"] != only:
                continue
            self.core._set_mode_in(c, ln["device_id"], mode, self._session(m, ln), now)

    def start(self, mid: int, by: str) -> None:
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] != "ready":
                raise MatchError("only a match that has not started can be started")
            fmt = json.loads(m["format_json"])
            for ln in self._lanes(c, mid):        # a fresh match series per lane, sized to the format
                sid = self.core._set_mode_in(c, ln["device_id"], "sighter", self._session(m, ln), now, new_series=True)
                c.execute("UPDATE series SET total=? WHERE id=?", (mf.record_shots(fmt), sid))
                c.execute("UPDATE match_lanes SET series_id=? WHERE match_id=? AND device_id=?", (sid, mid, ln["device_id"]))
            c.execute("UPDATE matches SET state='running', started_ms=? WHERE id=?", (now, mid))
            self._log(c, mid, now, "start", None, None, {"by": by})
            self._enter(c, self._row(c, mid), 0, now)
        self._pub(mid)

    def _enter(self, c, m, i: int, now: int) -> None:
        """Start phase i now, or (officer pacing) hold at its start until the range officer presses Next."""
        fmt = json.loads(m["format_json"])
        p = fmt["phases"][i]
        hold = m["paced"] == "officer"
        c.execute("UPDATE matches SET phase=?, state=?, phase_started_ms=?, phase_used_ms=0, phase_extra_ms=0, ended_ms=NULL "
                  "WHERE id=?", (i, "holding" if hold else "running", None if hold else now, m["id"]))
        self._log(c, m["id"], now, "phase_start", i, None, {"hold": True} if hold else None)
        if hold:
            self._set_modes(c, m, "sighter", now)            # nothing counts as record before START
        else:
            self._set_modes(c, m, "record" if p["kind"] == "record" else "sighter", now)

    def _finish_phase(self, c, m, now: int, how: str) -> None:
        fmt = json.loads(m["format_json"])
        self._log(c, m["id"], now, "phase_end", m["phase"], None, {"how": how})
        nxt = m["phase"] + 1
        if nxt < len(fmt["phases"]):
            self._enter(c, m, nxt, now)
        else:
            c.execute("UPDATE matches SET state='done', ended_ms=?, phase_started_ms=NULL WHERE id=?", (now, m["id"]))
            self._log(c, m["id"], now, "end", None, None)
            self._set_modes(c, m, "sighter", now)       # nothing after STOP counts as record

    def remaining_ms(self, m, now: int) -> int:
        if m["phase"] < 0 or m["state"] not in LIVE:
            return 0
        p = json.loads(m["format_json"])["phases"][m["phase"]]
        used = m["phase_used_ms"] + (now - m["phase_started_ms"] if m["state"] == "running" and m["phase_started_ms"] else 0)
        return max(0, p["seconds"] * 1000 + m["phase_extra_ms"] - used)

    def tick(self) -> int:
        """End every phase whose time is up. Returns how many phases ended (tests use it)."""
        now = self.core.now_ms()
        ended = 0
        ids = [r["id"] for r in self.db.conn().execute("SELECT id FROM matches WHERE state='running'")]
        for mid in ids:
            with self.db.write() as c:
                m = self._row(c, mid)
                while m["state"] == "running" and self.remaining_ms(m, now) <= 0:
                    # the phase ended at its exact end time, not at the tick: the timeline stays exact
                    p_end = now - (self._overrun(m, now))
                    self._finish_phase(c, m, p_end, "time")
                    ended += 1
                    m = self._row(c, mid)
            if ended:
                self._pub(mid)
        return ended

    def _overrun(self, m, now: int) -> int:
        p = json.loads(m["format_json"])["phases"][m["phase"]]
        used = m["phase_used_ms"] + (now - m["phase_started_ms"])
        return max(0, used - (p["seconds"] * 1000 + m["phase_extra_ms"]))

    def command(self, mid: int, action: str, by: str, seconds: Optional[int] = None, device_id: Optional[str] = None,
                paced: Optional[str] = None) -> None:
        """Range officer actions: go (START a held phase), pause, resume, next (end the phase now), extra (add seconds
        to this phase), abort, start_record (one lane, in an NRA-style stage). restart (to phase `seconds`... see
        restart()) is separate because only a director may do it."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] not in LIVE:
                raise MatchError("the match is not running")
            fmt = json.loads(m["format_json"])
            p = fmt["phases"][m["phase"]]
            if action == "pacing":                    # switch how commands are given; takes effect at the next phase
                if paced not in PACING:
                    raise MatchError("commands: spoken, officer or auto")
                c.execute("UPDATE matches SET paced=? WHERE id=?", (paced, mid))
                self._log(c, mid, now, "pacing", m["phase"], None, {"paced": paced, "by": by})
            elif m["state"] == "holding" and action not in ("go", "next", "abort"):
                raise MatchError("the phase has not started: press Next to start it")
            elif action == "go":
                if m["state"] != "holding":
                    raise MatchError("nothing is waiting to start")
                c.execute("UPDATE matches SET state='running', phase_started_ms=? WHERE id=?", (now, mid))
                self._log(c, mid, now, "go", m["phase"], None, {"by": by})
                self._set_modes(c, m, "record" if p["kind"] == "record" else "sighter", now)
            elif action == "pause":
                if m["state"] != "running":
                    raise MatchError("already paused")
                c.execute("UPDATE matches SET state='paused', phase_used_ms=?, phase_started_ms=NULL WHERE id=?",
                          (m["phase_used_ms"] + now - m["phase_started_ms"], mid))
                self._log(c, mid, now, "pause", m["phase"], None, {"by": by})
            elif action == "resume":
                if m["state"] != "paused":
                    raise MatchError("not paused")
                c.execute("UPDATE matches SET state='running', phase_started_ms=? WHERE id=?", (now, mid))
                self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
            elif action == "next":
                if m["state"] in ("paused", "holding"):    # ending a paused or held phase: the pause ends with it
                    self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
                    c.execute("UPDATE matches SET state='running', phase_started_ms=? WHERE id=?", (now, mid))
                self._finish_phase(c, self._row(c, mid), now, "officer")
            elif action == "extra":
                if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 3600:
                    raise MatchError("extra time: 1 to 3600 seconds")
                c.execute("UPDATE matches SET phase_extra_ms=phase_extra_ms+? WHERE id=?", (seconds * 1000, mid))
                self._log(c, mid, now, "extra", m["phase"], None, {"seconds": seconds, "by": by})
            elif action == "abort":
                self._log(c, mid, now, "phase_end", m["phase"], None, {"how": "abort"})
                c.execute("UPDATE matches SET state='aborted', ended_ms=?, phase_started_ms=NULL WHERE id=?", (now, mid))
                self._log(c, mid, now, "abort", m["phase"], None, {"by": by})
                self._set_modes(c, m, "sighter", now)
            elif action == "start_record":
                if p["kind"] != "stage" or m["state"] != "running":
                    raise MatchError("the shooter starts record only during a running sighting-and-record stage")
                ln = c.execute("SELECT * FROM match_lanes WHERE match_id=? AND device_id=?", (mid, device_id)).fetchone()
                if ln is None:
                    raise MatchError("that target is not in this match")
                if self._switched(c, mid, m["phase"], device_id):
                    raise MatchError("already in record for this stage")
                self._log(c, mid, now, "start_record", m["phase"], device_id, {"by": by})
                self._set_modes(c, m, "record", now, only=device_id)
            else:
                raise MatchError("unknown action")
        self._pub(mid)

    def restart(self, mid: int, phase: int, by: str) -> None:
        """Go back to the start of `phase` (0 = the whole match), also after the match ended. The current phase ends
        now; shots fired since that phase first started stay in the record and are flagged for a decision."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] == "ready":
                raise MatchError("the match has not started")
            fmt = json.loads(m["format_json"])
            if not isinstance(phase, int) or isinstance(phase, bool) or not 0 <= phase < len(fmt["phases"]):
                raise MatchError("no such phase")
            if m["state"] in LIVE:
                if m["state"] == "paused":
                    self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
                self._log(c, mid, now, "phase_end", m["phase"], None, {"how": "restart"})
            self._log(c, mid, now, "restart", phase, None, {"by": by, "from": m["phase"], "state": m["state"]})
            self._enter(c, self._row(c, mid), phase, now)
        self._pub(mid)

    @staticmethod
    def _switched(c, mid, phase, device_id) -> bool:
        start = c.execute("SELECT MAX(id) FROM match_log WHERE match_id=? AND kind='phase_start' AND phase=?", (mid, phase)).fetchone()[0]
        return c.execute("SELECT 1 FROM match_log WHERE match_id=? AND kind='start_record' AND phase=? AND device_id=? AND id>?",
                         (mid, phase, device_id, start or 0)).fetchone() is not None

    def decide(self, mid: int, shot_id: int, decision: str, by: str) -> None:
        flags = {f["shot_id"]: f for f in self.detail(mid)["flags"]}
        if shot_id not in flags:
            raise MatchError("that shot is not flagged in this match")
        allowed = DECISIONS if flags[shot_id]["stream"] == "scored" else SIGHTER_DECISIONS
        if decision not in allowed and decision != "undecided":
            raise MatchError(f"decision must be one of {', '.join(allowed)}")
        with self.db.write() as c:
            if decision == "undecided":
                c.execute("DELETE FROM shot_decisions WHERE shot_id=?", (shot_id,))
            else:
                c.execute("INSERT INTO shot_decisions (shot_id, match_id, decision, decided_by, at_ms) VALUES (?,?,?,?,?) "
                          "ON CONFLICT(shot_id) DO UPDATE SET decision=excluded.decision, decided_by=excluded.decided_by, "
                          "at_ms=excluded.at_ms", (shot_id, mid, decision, by, self.core.now_ms()))
        self._pub(mid)

    # ------------------------------------------------------------------------------------------- reading
    def windows(self, c, mid) -> List[dict]:
        """The timeline as intervals: {phase, attempt, start, end, live, hold, superseded}. live False = the clock was
        paused (hold True: waiting for START). superseded: a later restart went back to this phase or an earlier one."""
        out, cur, attempts, restarts = [], None, {}, []
        for r in c.execute("SELECT * FROM match_log WHERE match_id=? ORDER BY id", (mid,)):
            k = r["kind"]
            data = json.loads(r["data"]) if r["data"] else {}
            if k == "phase_start":
                attempts[r["phase"]] = attempts.get(r["phase"], 0) + 1
                cur = {"phase": r["phase"], "attempt": attempts[r["phase"]], "start": r["at_ms"], "live": not data.get("hold"),
                       "hold": bool(data.get("hold")), "start_id": r["id"]}
            elif k in ("pause", "resume", "go") and cur is not None:
                cur["end"] = r["at_ms"]
                out.append(cur)
                cur = dict(cur, start=r["at_ms"], live=k != "pause", hold=False)
            elif k == "phase_end" and cur is not None:
                cur["end"] = r["at_ms"]
                out.append(cur)
                cur = None
            elif k == "restart":
                restarts.append((r["at_ms"], r["phase"], r["id"]))
        if cur is not None:
            cur["end"] = None                                  # still open
            out.append(cur)
        for w in out:                                     # by log order, exact even when times are equal
            w["superseded"] = any(rid > w["start_id"] and to <= w["phase"] for _, to, rid in restarts)
        return out

    def flags(self, c, m) -> List[dict]:
        """Every shot of the match's lanes that the timeline says did not happen in its time, or is a record shot
        beyond the stage's count. Recomputed from the facts every time."""
        fmt = json.loads(m["format_json"])
        wins = self.windows(c, m["id"])
        first = wins[0]["start"] if wins else None
        attempt_start: Dict[tuple, int] = {}
        for w in wins:
            attempt_start.setdefault((w["phase"], w["attempt"]), w["start"])
        out = []
        decisions = {r["shot_id"]: r["decision"] for r in c.execute("SELECT * FROM shot_decisions WHERE match_id=?", (m["id"],))}
        for ln in self._lanes(c, m["id"]):
            if ln["series_id"] is None:
                continue
            switches = [(r["phase"], r["at_ms"]) for r in c.execute(
                "SELECT phase, at_ms FROM match_log WHERE match_id=? AND kind='start_record' AND device_id=?", (m["id"], ln["device_id"]))]
            counted: Dict[tuple, int] = {}
            for s in c.execute("SELECT id, event_ms, stream, series_no, is_miss, tenths, ring FROM shots WHERE series_id=? ORDER BY event_ms, id",
                               (ln["series_id"],)):
                t = s["event_ms"]
                w = next((w for w in wins if w["start"] <= t and (w["end"] is None or t < w["end"])), None)
                why = None
                if w is None:
                    why = "before the start" if first is None or t < first else "after the end"
                elif w["superseded"]:
                    why = f"fired before {fmt['phases'][w['phase']]['label'].lower()} was restarted"
                elif w["hold"]:
                    why = f"before START of {fmt['phases'][w['phase']]['label'].lower()}"
                elif not w["live"]:
                    why = "while the clock was paused"
                else:
                    p = fmt["phases"][w["phase"]]
                    rec = s["stream"] == "scored"
                    if p["kind"] not in mf.FIRE_KINDS:
                        why = f"during {p['label'].lower()} (no firing)"
                    elif p["kind"] == "sight" and rec:
                        why = f"a record shot during {p['label'].lower()}"
                    elif p["kind"] == "record" and not rec:
                        why = f"a sighting shot during {p['label'].lower()}"
                    elif p["kind"] == "stage" and not rec and any(ph == w["phase"] and at >= attempt_start[(ph, w["attempt"])] and t > at
                                                                    for ph, at in switches):
                        why = "a sighting shot after record started"
                    elif rec:
                        key = (w["phase"], w["attempt"])
                        counted[key] = counted.get(key, 0) + 1
                        if counted[key] > p["shots"]:
                            why = f"more than {p['shots']} record shots in {p['label'].lower()}"
                if why:
                    out.append({"shot_id": s["id"], "device_id": ln["device_id"], "lane": ln["lane"], "n": s["series_no"],
                                "stream": s["stream"], "why": why, "event_ms": t, "decision": decisions.get(s["id"]),
                                "miss": bool(s["is_miss"]), "decimal": s["tenths"] / 10.0, "ring": s["ring"]})
        return out

    def _clock(self, m, now: int) -> dict:
        fmt = json.loads(m["format_json"])
        i = m["phase"]
        p = fmt["phases"][i] if 0 <= i < len(fmt["phases"]) else None
        nxt = fmt["phases"][i + 1] if p and i + 1 < len(fmt["phases"]) else None
        rem = self.remaining_ms(m, now)
        prev = fmt["phases"][i - 1] if p and i > 0 else None
        return {"state": m["state"], "paced": m["paced"], "phase": i, "phase_count": len(fmt["phases"]), "label": p["label"] if p else None,
                "prev_end_say": prev["end"] if prev else "",
                "kind": p["kind"] if p else None, "position": p["position"] if p else None,
                "shots": p["shots"] if p else 0, "seconds": p["seconds"] if p else 0, "extra_ms": m["phase_extra_ms"],
                "remaining_ms": rem, "ends_at_ms": now + rem if m["state"] == "running" else None,
                "next_start_say": nxt["start"] if nxt else "",
                "next": nxt["label"] if nxt else None, "warnings": p["warnings"] if p else [],
                "start_say": p["start"] if p else "", "end_say": p["end"] if p else "", "server_time_ms": now}

    def detail(self, mid: int) -> dict:
        now = self.core.now_ms()
        c = self.db.conn()
        m = self._row(c, mid)
        fmt = json.loads(m["format_json"])
        lanes = []
        for ln in self._lanes(c, mid):
            t = c.execute("SELECT name, last_seen_ms FROM targets WHERE device_id=?", (ln["device_id"],)).fetchone()
            counts = {"sighter": 0, "scored": 0}
            if ln["series_id"] is not None:
                for r in c.execute("SELECT stream, COUNT(*) AS n FROM shots WHERE series_id=? GROUP BY stream", (ln["series_id"],)):
                    counts[r["stream"]] = r["n"]
            lanes.append({"device_id": ln["device_id"], "lane": ln["lane"], "competitor": ln["competitor"],
                          "target": (t["name"] if t and t["name"] else ln["device_id"]), "series_id": ln["series_id"],
                          "online": bool(t) and now - t["last_seen_ms"] <= self.core.offline_after_ms,
                          "sighters": counts["sighter"], "record": counts["scored"],
                          "in_record": self._switched(c, mid, m["phase"], ln["device_id"]) if m["phase"] >= 0 else False})
        return {"id": m["id"], "name": m["name"], "format": fmt, "state": m["state"], "created_by": m["created_by"],
                "created_ms": m["created_ms"], "started_ms": m["started_ms"], "ended_ms": m["ended_ms"],
                "record_shots": mf.record_shots(fmt), "clock": self._clock(m, now), "lanes": lanes,
                "flags": self.flags(c, m) if m["state"] != "ready" else []}

    def list(self, limit: int = 30) -> List[dict]:
        now = self.core.now_ms()
        out = []
        for m in self.db.conn().execute("SELECT * FROM matches ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 200)),)):
            fmt = json.loads(m["format_json"])
            out.append({"id": m["id"], "name": m["name"], "state": m["state"], "format_name": fmt["name"],
                        "created_ms": m["created_ms"], "clock": self._clock(m, now)})
        return out

    def target_info(self, c, device_id: str, now: int) -> Optional[dict]:
        """What a target's own screen shows during a match (no login needed: read only)."""
        r = c.execute("""SELECT m.*, l.lane, l.competitor FROM matches m JOIN match_lanes l ON l.match_id=m.id
                         WHERE l.device_id=? AND m.state IN ('ready','running','paused','holding') ORDER BY m.id DESC LIMIT 1""",
                      (device_id,)).fetchone()
        if r is None:
            return None
        clock = self._clock(r, now)
        clock.update({"match_id": r["id"], "name": r["name"], "lane": r["lane"], "competitor": r["competitor"],
                      "in_record": self._switched(c, r["id"], r["phase"], device_id) if r["phase"] >= 0 else False})
        return clock

    # ------------------------------------------------------------------------------------------- results
    def results(self, mid: int) -> dict:
        """Standings of one match. A record shot counts unless it is flagged; a flagged shot counts only after the
        match director decided: count (as fired), miss (0), penalty2 (2 points off, not below 0); annul and undecided
        do not count. Ranking (PROVISIONAL until each rulebook's tie-break rules are built): total, then inner tens
        (decimal scoring) or X count (integer), then 10-shot series from the last one backwards."""
        c = self.db.conn()
        m = self._row(c, mid)
        fmt = json.loads(m["format_json"])
        decimal = fmt.get("scoring") == "decimal"
        flags = {f["shot_id"]: f for f in (self.flags(c, m) if m["state"] != "ready" else [])}
        rows = []
        for ln in self._lanes(c, mid):
            vals, xs = [], 0
            first_pen = [f["decision"] for f in flags.values() if f["device_id"] == ln["device_id"] and f["stream"] == "sighter"
                         and f["decision"] in ("penalty2_first", "miss_first")]
            if ln["series_id"] is not None:
                for s in c.execute("SELECT id, is_miss, tenths, ring FROM shots WHERE series_id=? AND stream='scored' ORDER BY event_ms, id",
                                   (ln["series_id"],)):
                    f = flags.get(s["id"])
                    decision = f["decision"] if f else "count"
                    if decision not in ("count", "miss", "penalty2"):
                        continue
                    v = 0 if s["is_miss"] else (s["tenths"] if decimal else s["ring"] * 10)   # in tenths
                    if decision == "miss":
                        v = 0
                    elif decision == "penalty2":
                        v = max(0, v - 20)
                    if decision == "count" and not s["is_miss"] and is_inner(s["tenths"]):
                        xs += 1
                    vals.append(v)
            if vals and first_pen:                        # penalties for stray sighting shots land on the first record shot
                if "miss_first" in first_pen:
                    vals[0] = 0
                vals[0] = max(0, vals[0] - 20 * first_pen.count("penalty2_first"))
            series = [sum(vals[i:i + 10]) for i in range(0, len(vals), 10)]
            t = c.execute("SELECT name FROM targets WHERE device_id=?", (ln["device_id"],)).fetchone()
            rows.append({"lane": ln["lane"], "device_id": ln["device_id"], "competitor": ln["competitor"] or "",
                         "target": (t["name"] if t and t["name"] else ln["device_id"]),
                         "total": sum(vals) / 10.0, "x": xs, "shots": len(vals), "series": [x / 10.0 for x in series],
                         "last10": [v / 10.0 for v in vals[-10:]],
                         "pending": sum(1 for f in flags.values() if f["device_id"] == ln["device_id"] and not f["decision"])})
        rows.sort(key=lambda r: (-r["total"], -r["x"], [-x for x in reversed(r["series"])], r["lane"]))
        for i, r in enumerate(rows):
            tied = i and (r["total"], r["x"], r["series"][::-1]) == (rows[i - 1]["total"], rows[i - 1]["x"], rows[i - 1]["series"][::-1])
            r["place"] = rows[i - 1]["place"] if tied else i + 1
        return {"id": mid, "name": m["name"], "format_name": fmt["name"], "scoring": fmt.get("scoring"),
                "record_shots": mf.record_shots(fmt), "state": m["state"], "clock": self._clock(m, self.core.now_ms()),
                "provisional_ranking": True, "rows": rows}

    def board(self, ids: Optional[List[int]] = None) -> List[dict]:
        """Public scoreboard data for the given matches, or for every active match and those finished in the last 6 h."""
        c = self.db.conn()
        if not ids:
            since = self.core.now_ms() - 6 * 3600 * 1000
            ids = [r["id"] for r in c.execute("SELECT id FROM matches WHERE state IN ('running','paused','holding','ready') "
                                               "OR (state='done' AND ended_ms>?) ORDER BY id", (since,))]
        out = []
        for mid in ids[:20]:
            try:
                out.append(self.results(int(mid)))
            except (MatchError, ValueError):
                continue
        return out


def is_inner(tenths: int) -> bool:
    from .scoring import is_inner_ten
    return is_inner_ten(tenths)
