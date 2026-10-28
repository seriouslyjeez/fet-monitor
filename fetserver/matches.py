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

Moving a shooter to another target during a match (ISSF 6.10.9.2 / 6.11.3.2, CMP 7.18.3, NRA 9.2 b / 10.3.2): the
lane's series goes with them to the new target (their shots so far stay theirs), their own time stands still from the
move until the range officer says they are ready, they then get the rulebook's extra time (ISSF 5 min, CMP 2 min, NRA
none: the time lost is already given back by the stopped clock) and unlimited sighters until they start record again.
When the match's phase ends before their own time does, they finish it on their own clock ("own" phase) while the
match goes on; a match whose time is over while someone is still finishing is in state 'overtime'.

Shots are never dropped or changed here: a shot that the timeline says was out of time (or one record shot too many)
is FLAGGED, and does not count until the match director decides (count / annul / miss / -2 points).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from . import matchformats as mf
from . import ranking
from .rules import guidance, lookup_text
from .admin import ROLES, USERNAME_RE, MIN_PASSWORD, hash_password

ACTIVE = ("ready", "running", "paused", "holding", "overtime")
LIVE = ("running", "paused", "holding")
ACTIVE_SQL = "('ready','running','paused','holding','overtime')"
MOVE_EXTRA_S = {"ISSF": 300, "CMP": 120, "NRA": 0}
REJOIN_EXTRA_MS = 120_000          # CMP 7.18.3: the time left when interrupted plus 2 minutes, on the later relay   # extra time after a move to another target, by tie-break rule set
PACING = ("spoken", "officer", "auto")   # spoken: the console speaks every command (default); officer: the range officer
# reads them and the clock waits for Next at every phase; auto: phases follow each other with no commands at all
DECISIONS = ("count", "annul", "miss", "penalty2", "annul_penalty2")   # for a flagged RECORD shot; the last: too many
# shots, annulled and 2 points off the lowest shot of the first series (ISSF 6.11.5, CMP 7.17.8 a)
TOO_MANY_RULE = {"ISSF": "ISSF 6.11.5", "CMP": "CMP 7.17.8", "NRA": "too many shots"}
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
                         WHERE l.device_id=? AND m.state IN """ + ACTIVE_SQL, (device_id,)).fetchone()
        return r["name"] if r else None

    def create(self, name, fmt_id, lanes, by: str, override: Optional[dict] = None, paced: str = "spoken",
               fmt: Optional[dict] = None, competition_id: Optional[int] = None, relay: Optional[int] = None) -> int:
        """A match in state 'ready'. lanes: [{"device_id", "competitor"}] in firing-point order. override: a full format
        (e.g. the standard one with a longer first sighting period, CMP 5.3.1 d); it is marked as modified."""
        if not isinstance(name, str) or not name.strip():
            raise MatchError("the match needs a name")
        base = fmt if fmt is not None else (self.format(fmt_id) if isinstance(fmt_id, str) else None)   # fmt: a competition's own
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
            mid = c.execute("INSERT INTO matches (name, format_json, state, created_ms, created_by, paced, competition_id, relay) "
                            "VALUES (?,?,?,?,?,?,?,?)", (name.strip()[:80], json.dumps(fmt), "ready", now, by, paced, competition_id, relay)).lastrowid
            for i, ln in enumerate(lanes, 1):
                comp = ln.get("competitor")
                comp = comp.strip()[:60] if isinstance(comp, str) and comp.strip() else None
                c.execute("INSERT INTO match_lanes (match_id, device_id, lane, competitor, competitor_id) VALUES (?,?,?,?,?)",
                          (mid, ln["device_id"], i, comp, ln.get("competitor_id")))
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

    def _set_modes(self, c, m, mode: str, now: int, only: Optional[str] = None, everyone: bool = False) -> None:
        """Switch the lanes' targets; a lane on its own clock (moved shooter) is left alone unless named or everyone.
        A lane that left for a later relay (CMP 7.18) is never switched; one that rejoins at a later phase stays in
        sighting until then."""
        cur = c.execute("SELECT phase, format_json FROM matches WHERE id=?", (m["id"],)).fetchone()
        phase = cur["phase"]
        phases = json.loads(cur["format_json"])["phases"]
        only_lanes = phases[phase].get("lanes") if 0 <= phase < len(phases) else None     # a shoot-off: only the tied fire
        for ln in self._lanes(c, m["id"]):
            if only and ln["device_id"] != only:
                continue
            if ln["left_ms"] is not None:
                continue
            if not only and not everyone and own_clock(ln):
                continue
            out = (ln["start_phase"] > phase and not everyone) or (only_lanes is not None and ln["lane"] not in only_lanes and not only)
            self.core._set_mode_in(c, ln["device_id"], "sighter" if out else mode, self._session(m, ln), now)

    @staticmethod
    def _clear_lanes(c, mid) -> None:
        c.execute("UPDATE match_lanes SET moving_since_ms=NULL, moving_left_ms=NULL, extra_ms=0, own_phase=NULL, "
                  "own_left_ms=NULL, own_since_ms=NULL, sighting=0 WHERE match_id=?", (mid,))

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
        hold = m["paced"] == "officer" and not p.get("nohold")       # finals: START follows LOAD without a hold
        c.execute("UPDATE matches SET phase=?, state=?, phase_started_ms=?, phase_used_ms=0, phase_extra_ms=0, ended_ms=NULL "
                  "WHERE id=?", (i, "holding" if hold else "running", None if hold else now, m["id"]))
        c.execute("UPDATE match_lanes SET extra_ms=0, sighting=0 WHERE match_id=? AND own_phase IS NULL AND moving_since_ms IS NULL",
                  (m["id"],))
        c.execute("UPDATE match_lanes SET extra_ms=?, sighting=? WHERE match_id=? AND start_phase=? AND from_match IS NOT NULL",
                  ((REJOIN_EXTRA_MS - p["seconds"] * 1000), 1 if p["kind"] in ("record", "stage") else 0, m["id"], i))
        c.execute("UPDATE match_lanes SET extra_ms=extra_ms+carry_ms WHERE match_id=? AND start_phase=? AND from_match IS NOT NULL", (m["id"], i))
        self._log(c, m["id"], now, "phase_start", i, None, {"hold": True} if hold else None)
        if hold:
            self._set_modes(c, m, "sighter", now)            # nothing counts as record before START
        else:
            self._set_modes(c, m, "record" if p["kind"] == "record" else "sighter", now)

    def _finish_phase(self, c, m, now: int, how: str) -> None:
        fmt = json.loads(m["format_json"])
        self._log(c, m["id"], now, "phase_end", m["phase"], None, {"how": how})
        c.execute("UPDATE match_lanes SET own_phase=NULL, own_left_ms=NULL, sighting=0, extra_ms=0 WHERE match_id=? AND own_phase=? "
                  "AND own_left_ms=0 AND own_since_ms IS NULL AND moving_since_ms IS NULL", (m["id"], m["phase"]))   # their time ended early
        for ln in self._lanes(c, m["id"]):                # a moved shooter with time left finishes this phase on their own
            if ln["left_ms"] is not None:
                continue
            if ln["own_phase"] is None and (ln["moving_since_ms"] is not None or ln["extra_ms"] > 0):
                moving = ln["moving_since_ms"] is not None
                c.execute("UPDATE match_lanes SET own_phase=?, own_left_ms=?, own_since_ms=?, extra_ms=0 WHERE match_id=? AND device_id=?",
                          (m["phase"], None if moving else ln["extra_ms"], None if moving else now, m["id"], ln["device_id"]))
                self._log(c, m["id"], now, "lane_overtime", m["phase"], ln["device_id"], {"left_ms": None if moving else ln["extra_ms"]})
            elif ln["own_phase"] is None and ln["sighting"]:          # no time of their own left: they go on with everyone
                c.execute("UPDATE match_lanes SET sighting=0 WHERE match_id=? AND device_id=?", (m["id"], ln["device_id"]))
        nxt = m["phase"] + 1
        if nxt < len(fmt["phases"]):
            self._enter(c, m, nxt, now)
        elif any(own_clock(ln) for ln in self._lanes(c, m["id"])):
            c.execute("UPDATE matches SET state='overtime', phase_started_ms=?, ended_ms=NULL WHERE id=?", (now, m["id"]))
            self._log(c, m["id"], now, "overtime", m["phase"], None)
            self._set_modes(c, m, "sighter", now)       # everyone else: nothing after STOP counts as record
        else:
            self._done(c, m, now)

    def _done(self, c, m, now: int) -> None:
        c.execute("UPDATE matches SET state='done', ended_ms=?, phase_started_ms=NULL WHERE id=?", (now, m["id"]))
        self._log(c, m["id"], now, "end", None, None)
        self._set_modes(c, m, "sighter", now, everyone=True)   # nothing after STOP counts as record

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
        ids = [r["id"] for r in self.db.conn().execute(
            "SELECT id FROM matches WHERE state IN ('running','overtime') OR id IN (SELECT match_id FROM match_lanes WHERE own_since_ms IS NOT NULL)")]   # (running covers lanes with less time)
        for mid in ids:
            before = ended
            with self.db.write() as c:
                m = self._row(c, mid)
                while m["state"] == "running" and self.remaining_ms(m, now) <= 0:
                    # the phase ended at its exact end time, not at the tick: the timeline stays exact
                    p_end = now - (self._overrun(m, now))
                    self._finish_phase(c, m, p_end, "time")
                    ended += 1
                    m = self._row(c, mid)
                if m["state"] == "running" and self._all_fired(c, m):         # finals: STOP when everyone has fired
                    self._finish_phase(c, m, now + 1, "all fired")              # just after: the last shot is inside the shot's time
                    ended += 1
                ended += self._lane_tick(c, mid, now)
            if ended != before:
                self._pub(mid)
        return ended

    def _all_fired(self, c, m) -> bool:
        """A final's shot phase (early) ends as soon as every finalist in it has fired its shots (CMP 10.4.6 c)."""
        fmt = json.loads(m["format_json"])
        p = fmt["phases"][m["phase"]]
        if not p.get("early") or p["kind"] != "record":
            return False
        start = c.execute("SELECT MAX(at_ms) FROM match_log WHERE match_id=? AND kind IN ('phase_start','go') AND phase=?",
                          (m["id"], m["phase"])).fetchone()[0]
        lanes = [ln for ln in self._lanes(c, m["id"]) if ln["series_id"] is not None and ln["left_ms"] is None and not own_clock(ln)
                 and (p.get("lanes") is None or ln["lane"] in p["lanes"])]
        return bool(lanes) and all(c.execute("SELECT COUNT(*) FROM shots WHERE series_id=? AND stream='scored' AND event_ms>=?",
                                             (ln["series_id"], start)).fetchone()[0] >= p["shots"] for ln in lanes)

    def shoot_off(self, mid: int, lanes: List[int], by: str) -> None:
        """A tie-breaking shot for the tied firing points of a final (CMP 10.2.9 n), after the final's last shot: the
        match runs again for that one shot. Repeated until the tie is broken."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if not m["final_kind"]:
                raise MatchError("shoot-offs are for finals")
            if m["state"] != "done":
                raise MatchError("a shoot-off comes after the final's last shot")
            self.not_official(m)
            have = {ln["lane"] for ln in self._lanes(c, mid)}
            if not isinstance(lanes, list) or len(set(lanes)) < 2 or not set(lanes) <= have:
                raise MatchError("choose at least two firing points of this final")
            fmt = json.loads(m["format_json"])
            n = 1 + sum(1 for p in fmt["phases"] if p.get("lanes") is not None and p["kind"] == "record")
            fmt["phases"] += mf.shoot_off_phases(sorted(set(lanes)), n)
            if len(fmt["phases"]) > mf.MAX_PHASES:
                raise MatchError("too many shoot-offs")
            c.execute("UPDATE matches SET format_json=? WHERE id=?", (json.dumps(fmt), mid))
            self._log(c, mid, now, "shoot_off", len(fmt["phases"]) - 2, None, {"lanes": sorted(set(lanes)), "by": by})
            self._enter(c, self._row(c, mid), len(fmt["phases"]) - 2, now)
        self._pub(mid)

    def _lane_tick(self, c, mid, now: int) -> int:
        """End the own phase of every moved shooter whose own time is up (at its exact end), then the match if it was
        only waiting for them."""
        n, last = 0, None
        m0 = self._row(c, mid)
        for ln in self._lanes(c, mid):
            if ln["own_phase"] is None and ln["moving_since_ms"] is None and ln["extra_ms"] < 0 and m0["state"] == "running":
                rem = self.remaining_ms(m0, now) + ln["extra_ms"]
                if rem <= 0:                            # less time than the match (CMP 7.18.3): this shooter stops now
                    end = now + rem
                    c.execute("UPDATE match_lanes SET own_phase=?, own_left_ms=0, own_since_ms=NULL, extra_ms=0, sighting=0 "
                              "WHERE match_id=? AND device_id=?", (m0["phase"], mid, ln["device_id"]))
                    self._log(c, mid, end, "lane_stop", m0["phase"], ln["device_id"])
                    self._set_modes(c, m0, "sighter", end, only=ln["device_id"])
                    n += 1
                continue
            if ln["own_phase"] is not None and ln["moving_since_ms"] is None and ln["own_since_ms"] is not None:
                end = ln["own_since_ms"] + ln["own_left_ms"]
                if end <= now:
                    self._lane_end(c, self._row(c, mid), ln, end)
                    n, last = n + 1, max(last or end, end)
        m = self._row(c, mid)
        if m["state"] == "overtime" and not any(own_clock(ln) for ln in self._lanes(c, mid)):
            self._done(c, m, last if last is not None else now)
        return n

    def _lane_end(self, c, m, ln, at: int) -> None:
        """A moved shooter's own phase is over: they join whatever the match is doing now."""
        self._log(c, m["id"], at, "lane_end", ln["own_phase"], ln["device_id"])
        c.execute("UPDATE match_lanes SET own_phase=NULL, own_left_ms=NULL, own_since_ms=NULL, moving_since_ms=NULL, "
                  "moving_left_ms=NULL, extra_ms=0, sighting=0 WHERE match_id=? AND device_id=?", (m["id"], ln["device_id"]))
        p = json.loads(m["format_json"])["phases"][m["phase"]] if m["phase"] >= 0 else None
        mode = "record" if m["state"] in ("running", "paused") and p and p["kind"] == "record" else "sighter"
        self._set_modes(c, m, mode, at, only=ln["device_id"])

    def _lanes_pause(self, c, mid, now: int) -> None:
        for ln in self._lanes(c, mid):
            if ln["own_since_ms"] is not None:
                c.execute("UPDATE match_lanes SET own_left_ms=?, own_since_ms=NULL WHERE match_id=? AND device_id=?",
                          (max(0, ln["own_left_ms"] - (now - ln["own_since_ms"])), mid, ln["device_id"]))

    @staticmethod
    def _lanes_resume(c, mid, now: int) -> None:
        c.execute("UPDATE match_lanes SET own_since_ms=? WHERE match_id=? AND own_phase IS NOT NULL AND moving_since_ms IS NULL "
                  "AND own_left_ms > 0", (now, mid))

    def _overrun(self, m, now: int) -> int:
        p = json.loads(m["format_json"])["phases"][m["phase"]]
        used = m["phase_used_ms"] + (now - m["phase_started_ms"])
        return max(0, used - (p["seconds"] * 1000 + m["phase_extra_ms"]))

    def command(self, mid: int, action: str, by: str, seconds: Optional[int] = None, device_id: Optional[str] = None,
                paced: Optional[str] = None) -> None:
        """Range officer actions: go (START a held phase), pause, resume, next (end the phase now), extra (add seconds
        to this phase), abort, start_record (one lane, in an NRA-style stage). restart (to phase `seconds`... see
        restart()) is separate because only a director may do it."""
        if action == "start_record":
            return self.lane_command(mid, device_id, "start_record", by)
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] == "overtime":
                self._overtime_command(c, m, action, by, now)
                self._pub(mid)
                return
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
                self._lanes_pause(c, mid, now)
                self._log(c, mid, now, "pause", m["phase"], None, {"by": by})
            elif action == "interrupt":                    # the whole range stops (target failure, power, safety)
                if m["state"] != "running":
                    raise MatchError("the clock is not running")
                c.execute("UPDATE matches SET state='paused', phase_used_ms=?, phase_started_ms=NULL WHERE id=?",
                          (m["phase_used_ms"] + now - m["phase_started_ms"], mid))
                self._lanes_pause(c, mid, now)
                self._log(c, mid, now, "pause", m["phase"], None, {"by": by, "interruption": True})
            elif action == "interrupt_end":
                last = c.execute("SELECT * FROM match_log WHERE match_id=? AND kind='pause' ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
                if m["state"] != "paused" or last is None or not json.loads(last["data"] or "{}").get("interruption"):
                    raise MatchError("the range is not interrupted")
                lost = (now - last["at_ms"]) // 1000
                extra, sighters, rule = range_interruption(rules_of(fmt), lost)
                c.execute("UPDATE matches SET state='running', phase_started_ms=?, phase_extra_ms=phase_extra_ms+? WHERE id=?", (now, extra * 1000, mid))
                self._lanes_resume(c, mid, now)
                self._log(c, mid, now, "resume", m["phase"], None, {"by": by, "interruption": True, "lost_s": lost, "extra_s": extra, "sighters": sighters, "rule": rule})
                if sighters and p["kind"] in ("record", "stage"):        # unlimited sighters for everyone before record again
                    them = [ln["device_id"] for ln in self._lanes(c, mid) if not own_clock(ln) and ln["left_ms"] is None and ln["start_phase"] <= m["phase"]]
                    c.executemany("UPDATE match_lanes SET sighting=1 WHERE match_id=? AND device_id=?", [(mid, d) for d in them])
                    self._log(c, mid, now, "sighters", m["phase"], None, {"rule": rule})
                    for d in them:
                        self._set_modes(c, m, "sighter", now, only=d)
            elif action == "resume":
                if m["state"] != "paused":
                    raise MatchError("not paused")
                c.execute("UPDATE matches SET state='running', phase_started_ms=? WHERE id=?", (now, mid))
                self._lanes_resume(c, mid, now)
                self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
            elif action == "next":
                if m["state"] in ("paused", "holding"):    # ending a paused or held phase: the pause ends with it
                    if m["state"] == "paused":
                        self._lanes_resume(c, mid, now)
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
                self._abort(c, m, now, by)
            else:
                raise MatchError("unknown action")
        self._pub(mid)

    def _abort(self, c, m, now: int, by: str) -> None:
        c.execute("UPDATE matches SET state='aborted', ended_ms=?, phase_started_ms=NULL WHERE id=?", (now, m["id"]))
        self._log(c, m["id"], now, "abort", m["phase"], None, {"by": by})
        self._clear_lanes(c, m["id"])
        self._set_modes(c, m, "sighter", now, everyone=True)

    def _overtime_command(self, c, m, action: str, by: str, now: int) -> None:
        """The match time is over and only moved shooters are finishing: pause/resume their clocks, end now, or stop."""
        mid = m["id"]
        if action == "pause":
            if m["phase_started_ms"] is None:
                raise MatchError("already paused")
            self._lanes_pause(c, mid, now)
            c.execute("UPDATE matches SET phase_started_ms=NULL WHERE id=?", (mid,))
            self._log(c, mid, now, "pause", m["phase"], None, {"by": by})
        elif action == "resume":
            if m["phase_started_ms"] is not None:
                raise MatchError("not paused")
            self._lanes_resume(c, mid, now)
            c.execute("UPDATE matches SET phase_started_ms=? WHERE id=?", (now, mid))
            self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
        elif action == "next":
            if m["phase_started_ms"] is None:
                self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
            for ln in self._lanes(c, mid):
                if own_clock(ln):
                    self._lane_end(c, m, ln, now)
            self._done(c, self._row(c, mid), now)
        elif action == "abort":
            self._log(c, mid, now, "phase_end", m["phase"], None, {"how": "abort"})
            self._abort(c, m, now, by)
        else:
            raise MatchError("the match time is over: only moved shooters are still finishing")

    def restart(self, mid: int, phase: int, by: str) -> None:
        """Go back to the start of `phase` (0 = the whole match), also after the match ended. The current phase ends
        now; shots fired since that phase first started stay in the record and are flagged for a decision."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] == "ready":
                raise MatchError("the match has not started")
            self.not_official(m)
            fmt = json.loads(m["format_json"])
            if not isinstance(phase, int) or isinstance(phase, bool) or not 0 <= phase < len(fmt["phases"]):
                raise MatchError("no such phase")
            if m["state"] in LIVE:
                if m["state"] == "paused":
                    self._log(c, mid, now, "resume", m["phase"], None, {"by": by})
                self._log(c, mid, now, "phase_end", m["phase"], None, {"how": "restart"})
            self._log(c, mid, now, "restart", phase, None, {"by": by, "from": m["phase"], "state": m["state"]})
            self._clear_lanes(c, mid)                  # moved shooters start the phase again with everyone
            self._enter(c, self._row(c, mid), phase, now)
        self._pub(mid)

    def _switched(self, c, mid, phase, device_id) -> bool:
        """Has this lane started record in this phase (since its last start, and since it was last moved)?"""
        devs = self._lane_devs(c, mid, device_id)
        q = ",".join("?" * len(devs))
        start = c.execute("SELECT MAX(id) FROM match_log WHERE match_id=? AND kind='phase_start' AND phase=?", (mid, phase)).fetchone()[0]
        ready = c.execute(f"SELECT MAX(id) FROM match_log WHERE match_id=? AND kind IN ('lane_ready','sighters') AND (device_id IN ({q}) OR device_id IS NULL)",
                          (mid, *devs)).fetchone()[0]
        return c.execute(f"SELECT 1 FROM match_log WHERE match_id=? AND kind='start_record' AND phase=? AND device_id IN ({q}) AND id>?",
                         (mid, phase, *devs, max(start or 0, ready or 0))).fetchone() is not None

    @staticmethod
    def _lane_devs(c, mid, device_id) -> List[str]:
        """Every target a lane has used, newest first (a shooter may have been moved)."""
        devs, cur = [device_id], device_id
        for r in c.execute("SELECT device_id, data FROM match_log WHERE match_id=? AND kind='move' ORDER BY id DESC", (mid,)):
            if r["device_id"] == cur:
                cur = json.loads(r["data"])["from"]
                devs.append(cur)
        return devs

    # ------------------------------------------------------------------------------------------- moving a shooter
    def lane_left_ms(self, m, ln, now: int) -> int:
        """A lane's own time left in its phase."""
        if ln["moving_since_ms"] is not None:
            return ln["moving_left_ms"] or 0
        if ln["own_phase"] is not None:
            return max(0, ln["own_left_ms"] - (now - ln["own_since_ms"] if ln["own_since_ms"] is not None else 0))
        return self.remaining_ms(m, now) + ln["extra_ms"] if m["state"] in LIVE else 0

    def _lane(self, c, mid, device_id):
        ln = c.execute("SELECT * FROM match_lanes WHERE match_id=? AND device_id=?", (mid, device_id)).fetchone()
        if ln is None:
            raise MatchError("that target is not in this match")
        return ln

    def move(self, mid: int, from_dev: str, to_dev: str, by: str) -> None:
        """Move a lane to another target that is in no other match. Before the start: just the target. During the
        match: the shooter's series moves with them and their own time stands still until lane_command 'ready'."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] in ("done", "aborted"):
                raise MatchError("the match is over")
            self.not_official(m)
            ln = self._lane(c, mid, from_dev)
            if to_dev == from_dev:
                raise MatchError("choose another target")
            if not isinstance(to_dev, str) or c.execute("SELECT 1 FROM targets WHERE device_id=? AND removed=0 AND banned=0",
                                                        (to_dev,)).fetchone() is None:
                raise MatchError("unknown target")
            other = self.lock_of(c, to_dev)
            if other:
                raise MatchError(f"that target is in the match '{other}'")
            if m["state"] == "overtime" and not own_clock(ln):
                raise MatchError("this shooter has finished")
            phase = None
            if m["state"] != "ready":
                phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
                if ln["moving_since_ms"] is None:            # their time stands still from now
                    c.execute("UPDATE match_lanes SET moving_since_ms=?, moving_left_ms=?, own_since_ms=NULL, own_left_ms=NULL "
                              "WHERE match_id=? AND device_id=?", (now, self.lane_left_ms(m, ln, now), mid, from_dev))
                self.core._move_series(c, from_dev, to_dev, ln["series_id"], now)
            c.execute("UPDATE match_lanes SET device_id=? WHERE match_id=? AND device_id=?", (to_dev, mid, from_dev))
            self._log(c, mid, now, "move", phase, to_dev, {"from": from_dev, "by": by})
        self._pub(mid)

    def add_late(self, mid: int, device_id: str, competitor: Optional[str], by: str, competitor_id: Optional[int] = None) -> None:
        """A shooter who arrives late joins the running match on a free target, with no extra time (ISSF 6.11.4);
        after the preparation and sighting time they may still fire sighters, within their time."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] not in LIVE:
                raise MatchError("the match is not running")
            self.not_official(m)
            if not isinstance(device_id, str) or c.execute("SELECT 1 FROM targets WHERE device_id=? AND removed=0 AND banned=0", (device_id,)).fetchone() is None:
                raise MatchError("unknown target")
            other = self.lock_of(c, device_id)
            if other:
                raise MatchError(f"that target is in the match '{other}'")
            fmt = json.loads(m["format_json"])
            p = fmt["phases"][m["phase"]]
            lane = (c.execute("SELECT MAX(lane) FROM match_lanes WHERE match_id=?", (mid,)).fetchone()[0] or 0) + 1
            name = competitor.strip()[:60] if isinstance(competitor, str) and competitor.strip() else None
            c.execute("INSERT INTO match_lanes (match_id, device_id, lane, competitor, competitor_id) VALUES (?,?,?,?,?)",
                      (mid, device_id, lane, name, competitor_id))
            ln = self._lane(c, mid, device_id)
            sighting = p["kind"] in ("record", "stage")
            sid = self.core._set_mode_in(c, device_id, "sighter", self._session(m, ln), now, new_series=True)
            c.execute("UPDATE series SET total=? WHERE id=?", (mf.record_shots(fmt), sid))
            c.execute("UPDATE match_lanes SET series_id=?, sighting=? WHERE match_id=? AND device_id=?", (sid, 1 if sighting else 0, mid, device_id))
            self._log(c, mid, now, "late", m["phase"], device_id, {"competitor": name, "by": by, "rule": "ISSF 6.11.4"})
            if sighting:
                self._log(c, mid, now, "sighters", m["phase"], device_id, {"rule": "ISSF 6.11.4"})
        self._pub(mid)

    def _resume_lane(self, c, m, ln, p, left: int, sighters: bool, now: int) -> None:
        """A shooter whose time stood still goes on with `left` ms in their phase (sighters first if allowed)."""
        sighting = 1 if sighters and p["kind"] in ("record", "stage") else 0
        if ln["own_phase"] is not None:
            counting = m["state"] not in ("paused",) and not (m["state"] == "overtime" and m["phase_started_ms"] is None)
            c.execute("UPDATE match_lanes SET moving_since_ms=NULL, moving_left_ms=NULL, own_left_ms=?, own_since_ms=?, sighting=? "
                      "WHERE match_id=? AND device_id=?", (left, now if counting else None, sighting, m["id"], ln["device_id"]))
        else:
            c.execute("UPDATE match_lanes SET moving_since_ms=NULL, moving_left_ms=NULL, extra_ms=?, sighting=? "
                      "WHERE match_id=? AND device_id=?", (left - self.remaining_ms(m, now), sighting, m["id"], ln["device_id"]))
        mode = "sighter" if sighting or p["kind"] != "record" or m["state"] == "holding" else "record"
        self._set_modes(c, m, mode, now, only=ln["device_id"])

    def _stop_reason(self, c, mid, device_id) -> Optional[str]:
        """Why a lane's time stands still: "interrupt" / "malfunction", or None for a move (ended by Ready)."""
        devs = self._lane_devs(c, mid, device_id)
        q = ",".join("?" * len(devs))
        r = c.execute(f"SELECT kind, data FROM match_log WHERE match_id=? AND kind IN ('move','interrupt') AND device_id IN ({q}) "
                      "AND phase IS NOT NULL ORDER BY id DESC LIMIT 1", (mid, *devs)).fetchone()
        return json.loads(r["data"])["reason"] if r and r["kind"] == "interrupt" else None

    def lane_command(self, mid: int, device_id: str, action: str, by: str, seconds: Optional[int] = None) -> None:
        """One lane: 'ready' (a moved shooter is in position: their time runs again, plus `seconds` extra, default by
        the rulebook), 'extra' (seconds of extra time for this shooter only), 'start_record' (after sighters)."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] not in LIVE and m["state"] != "overtime":
                raise MatchError("the match is not running")
            ln = self._lane(c, mid, device_id)
            fmt = json.loads(m["format_json"])
            phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
            p = fmt["phases"][phase]
            if action == "ready":
                if ln["moving_since_ms"] is None:
                    raise MatchError("this shooter is not moving")
                if seconds is None:
                    seconds = MOVE_EXTRA_S[rules_of(fmt)]
                if not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 <= seconds <= 3600:
                    raise MatchError("extra time: 0 to 3600 seconds")
                left = ln["moving_left_ms"] + seconds * 1000
                self._resume_lane(c, m, ln, p, left, True, now)
                self._log(c, mid, now, "lane_ready", phase, device_id, {"extra_s": seconds, "left_ms": left, "by": by})
            elif action in ("interrupt", "malfunction") and (action == "interrupt" or rules_of(fmt) == "NRA"):
                # the shooter's time stops: an interruption (ISSF 6.11.3, CMP 7.18, NRA 9.2 b), or an NRA disabled rifle (9.5)
                if ln["moving_since_ms"] is not None:
                    raise MatchError("this shooter's time is already stopped")
                if m["state"] not in LIVE or m["state"] == "holding":
                    raise MatchError("the clock is not running")
                c.execute("UPDATE match_lanes SET moving_since_ms=?, moving_left_ms=?, own_since_ms=NULL, own_left_ms=NULL "
                          "WHERE match_id=? AND device_id=?", (now, self.lane_left_ms(m, ln, now), mid, device_id))
                self._log(c, mid, now, "interrupt", phase, device_id, {"reason": action, "by": by})
            elif action == "interrupt_end":
                if ln["moving_since_ms"] is None or self._stop_reason(c, mid, device_id) is None:
                    raise MatchError("this shooter is not interrupted")
                reason = self._stop_reason(c, mid, device_id)
                lost = (now - ln["moving_since_ms"]) // 1000
                left, sighters, rule = interruption_time(rules_of(fmt), reason, ln["moving_left_ms"], lost)
                self._resume_lane(c, m, ln, p, left, sighters, now)
                self._log(c, mid, now, "interrupt_end", phase, device_id, {"reason": reason, "lost_s": lost, "left_ms": left, "sighters": sighters, "rule": rule, "by": by})
                if sighters:
                    self._log(c, mid, now, "sighters", phase, device_id, {"rule": rule})
            elif action == "malfunction":                # ISSF 6.13.4, CMP 7.19.1: the clock keeps running
                book = rules_of(fmt)
                allowable = seconds == 1                 # ISSF: sighters only for an allowable malfunction (the RO ticks it)
                sighters = book == "CMP" or allowable
                extra = 120 if book == "CMP" and self.lane_left_ms(m, ln, now) <= 300_000 else 0     # after the 5-minute warning
                if extra:
                    col = "own_left_ms" if ln["own_phase"] is not None else "extra_ms"
                    c.execute(f"UPDATE match_lanes SET {col}={col}+? WHERE match_id=? AND device_id=?", (extra * 1000, mid, device_id))
                if sighters and p["kind"] in ("record", "stage"):
                    c.execute("UPDATE match_lanes SET sighting=1 WHERE match_id=? AND device_id=?", (mid, device_id))
                    self._set_modes(c, m, "sighter", now, only=device_id)
                rule = "CMP 7.19.1" if book == "CMP" else "ISSF 6.13.4"
                self._log(c, mid, now, "malfunction", phase, device_id, {"allowable": allowable, "sighters": sighters, "extra_s": extra, "rule": rule, "by": by})
                if sighters:
                    self._log(c, mid, now, "sighters", phase, device_id, {"rule": rule})
            elif action == "extra":
                if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 3600:
                    raise MatchError("extra time: 1 to 3600 seconds")
                col = "moving_left_ms" if ln["moving_since_ms"] is not None else "own_left_ms" if ln["own_phase"] is not None else "extra_ms"
                c.execute(f"UPDATE match_lanes SET {col}={col}+? WHERE match_id=? AND device_id=?", (seconds * 1000, mid, device_id))
                self._log(c, mid, now, "lane_extra", phase, device_id, {"seconds": seconds, "by": by})
            elif action == "start_record":
                if ln["moving_since_ms"] is not None:
                    raise MatchError("the shooter is moving to another target: press Ready first")
                counting = ln["own_phase"] is not None or m["state"] == "running"
                if ln["sighting"]:
                    if p["kind"] not in ("record", "stage") or not counting:
                        raise MatchError("record can start only while this shooter's time is running")
                else:
                    if p["kind"] != "stage" or not counting:
                        raise MatchError("the shooter starts record only during a running sighting-and-record stage")
                    if self._switched(c, mid, phase, device_id):
                        raise MatchError("already in record for this stage")
                c.execute("UPDATE match_lanes SET sighting=0 WHERE match_id=? AND device_id=?", (mid, device_id))
                self._log(c, mid, now, "start_record", phase, device_id, {"by": by})
                self._set_modes(c, m, "record", now, only=device_id)
            else:
                raise MatchError("unknown action")
        self._pub(mid)

    # ------------------------------------------------------------------------------------------- official results
    @staticmethod
    def not_official(m) -> None:
        """Official results do not change: decisions, corrections, restarts and moves wait until they are reopened."""
        if m["official_ms"] is not None:
            raise MatchError("the results are official: reopen them first")

    def open_items(self, c, m) -> List[str]:
        """What stops the results from being made official, in words (empty: nothing)."""
        out = []
        if m["state"] != "done":
            out.append("the match has not finished" if m["state"] != "aborted" else "the match was stopped")
        undecided = sum(1 for f in self.flags(c, m) if not f["decision"]) if m["state"] != "ready" else 0
        if undecided:
            out.append(f"{undecided} flagged shot{'s' if undecided != 1 else ''} not decided")
        n = self.core.corrections.open_count(c, m["id"])
        if n:
            out.append(f"{n} review{'s' if n != 1 else ''} or protest{'s' if n != 1 else ''} not settled")
        return out

    def protest_minutes(self, c, m) -> int:
        """The protest time after preliminary results: 10 minutes (ISSF 6.16.5.1, CMP 8.2); NRA: the challenge time
        the match sets (16.1 b), per competition, 30 minutes if not set."""
        if rules_of(json.loads(m["format_json"])) != "NRA":
            return 10
        r = c.execute("SELECT challenge_min FROM competitions WHERE id=?", (m["competition_id"],)).fetchone() if m["competition_id"] else None
        return r["challenge_min"] if r and r["challenge_min"] is not None else 30

    def post_results(self, mid: int, by: str) -> None:
        """Post preliminary results: the protest time starts now (posting again starts it again)."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] != "done":
                raise MatchError("the match has not finished")
            self.not_official(m)
            until = now + self.protest_minutes(c, m) * 60_000
            c.execute("UPDATE matches SET results_posted_ms=?, protest_until_ms=? WHERE id=?", (now, until, mid))
            self._log(c, mid, now, "posted", None, None, {"by": by, "protest_until_ms": until})
        self._pub(mid)

    def make_official(self, mid: int, by: str, override: bool = False) -> None:
        """Official results: nothing open, preliminary results posted, and the protest time over (unless the match
        director overrides it; logged)."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["official_ms"] is not None:
                raise MatchError("already official")
            todo = self.open_items(c, m)
            if todo:
                raise MatchError("not yet: " + "; ".join(todo))
            if m["results_posted_ms"] is None:
                raise MatchError("post the preliminary results first: the protest time starts then")
            running = m["protest_until_ms"] is not None and now < m["protest_until_ms"]
            if running and not override:
                raise MatchError("the protest time is not over")
            c.execute("UPDATE matches SET official_ms=?, official_by=? WHERE id=?", (now, by, mid))
            self._log(c, mid, now, "official", None, None, {"by": by, "protest_time_overridden": running})
        self._pub(mid)

    def reopen(self, mid: int, by: str) -> None:
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["official_ms"] is None:
                raise MatchError("the results are not official")
            c.execute("UPDATE matches SET official_ms=NULL, official_by=NULL, results_posted_ms=NULL, protest_until_ms=NULL WHERE id=?", (mid,))
            self._log(c, mid, now, "reopen", None, None, {"by": by, "was_official_ms": m["official_ms"]})
        self._pub(mid)

    def decide(self, mid: int, shot_id: int, decision: str, by: str) -> None:
        flags = {f["shot_id"]: f for f in self.detail(mid)["flags"]}
        if shot_id not in flags:
            raise MatchError("that shot is not flagged in this match")
        allowed = DECISIONS if flags[shot_id]["stream"] == "scored" else SIGHTER_DECISIONS
        if decision not in allowed and decision != "undecided":
            raise MatchError(f"decision must be one of {', '.join(allowed)}")
        with self.db.write() as c:
            self.not_official(self._row(c, mid))
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
        log = c.execute("SELECT * FROM match_log WHERE match_id=? ORDER BY id", (m["id"],)).fetchall()
        pauses = _pauses(log)
        for ln in self._lanes(c, m["id"]):
            if ln["series_id"] is None:
                continue
            devs = set(self._lane_devs(c, m["id"], ln["device_id"]))
            mine = [r for r in log if r["device_id"] in devs]
            switches = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] == "start_record"]
            readies = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] in ("lane_ready", "sighters")] + \
                [(r["phase"], r["at_ms"]) for r in log if r["kind"] == "sighters" and r["device_id"] is None]
            stops = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] == "lane_stop"]
            moving, own = _lane_spans(log, devs, wins)

            def sighting_ok(ph, a_start, t):          # after a move, or rejoining from an earlier relay (CMP 7.18.3):
                rejoin = [a_start] if ph == ln["start_phase"] and ln["from_match"] is not None else []   # unlimited sighters
                ready = max([at for q, at in readies if q == ph and a_start <= at <= t] + rejoin, default=None)   # until record starts
                return ready is not None and not any(q == ph and ready <= at < t for q, at in switches)

            def in_record(ph, a_start, t):            # an NRA-style stage: record started (and no move since)
                sw = max((at for q, at in switches if q == ph and at >= a_start and t > at), default=None)
                return sw is not None and not any(q == ph and sw <= at <= t for q, at in readies)
            counted: Dict[tuple, int] = {}
            eff = self.core.corrections.effects(c, m, ln["lane"])
            items = [dict(r) for r in c.execute("SELECT id, event_ms, stream, series_no, is_miss, tenths, ring FROM shots WHERE series_id=? ORDER BY event_ms, id",
                                                (ln["series_id"],)) if r["id"] not in eff["annulled"]]      # an annulled shot needs no flag
            items += [{"id": None, "event_ms": a["at_ms"], "stream": "scored"} for a in eff["added"]]     # added shots count, never flagged
            items.sort(key=lambda r: (r["event_ms"], r["id"] or 0))
            for s in items:
                t = s["event_ms"]
                inside = lambda sp: sp["start"] <= t and (sp["end"] is None or t < sp["end"])
                o = next((sp for sp in own if inside(sp)), None)
                if o is not None:                      # finishing their own phase after the match went on
                    w = {"phase": o["phase"], "attempt": o["attempt"], "start": o["start"], "end": o["end"], "hold": False,
                         "live": not any(inside(pz) for pz in pauses), "superseded": o["superseded"]}
                else:
                    w = next((w for w in wins if w["start"] <= t and (w["end"] is None or t < w["end"])), None)
                why, code, kind = None, None, None          # code + the phase's kind: what the rulebook guidance looks up
                stopped = next((sp for sp in moving if inside(sp)), None)
                if stopped is not None:
                    why, code = stopped["why"], "moving"
                elif w is None:
                    code = "before_start" if first is None or t < first else "after_end"
                    why = "before the start" if code == "before_start" else "after the end"
                elif w["superseded"]:
                    why, code = f"fired before {fmt['phases'][w['phase']]['label'].lower()} was restarted", "restarted"
                elif w["phase"] < ln["start_phase"]:
                    why, code = f"before this shooter rejoins at {fmt['phases'][ln['start_phase']]['label'].lower()} (CMP 7.18)", "waiting"
                elif any(ph == w["phase"] and at <= t and at >= attempt_start[(w["phase"], w["attempt"])] for ph, at in stops):
                    why, code = "after this shooter's own time (CMP 7.18.3)", "after_own_time"
                elif fmt["phases"][w["phase"]].get("lanes") is not None and ln["lane"] not in fmt["phases"][w["phase"]]["lanes"]:
                    why, code = f"during {fmt['phases'][w['phase']]['label'].lower()}, not in it", "not_in_shoot_off"
                elif w["hold"]:
                    kind = fmt["phases"][w["phase"]]["kind"]
                    why, code = f"before START of {fmt['phases'][w['phase']]['label'].lower()}", "before_cmd"
                elif not w["live"]:
                    why, code = "while the clock was paused", "paused"
                else:
                    p = fmt["phases"][w["phase"]]
                    rec = s["stream"] == "scored"
                    kind = p["kind"]
                    if p["kind"] not in mf.FIRE_KINDS:
                        why, code = f"during {p['label'].lower()} (no firing)", "no_fire"
                    elif p["kind"] == "sight" and rec:
                        why, code = f"a record shot during {p['label'].lower()}", "record_in_sight"
                    elif p["kind"] == "record" and not rec and not sighting_ok(w["phase"], attempt_start[(w["phase"], w["attempt"])], t):
                        why, code = f"a sighting shot during {p['label'].lower()}", "sighter_in_record"
                    elif p["kind"] == "stage" and not rec and in_record(w["phase"], attempt_start[(w["phase"], w["attempt"])], t):
                        why, code = "a sighting shot after record started", "sighter_after_record"
                    elif rec:
                        key = (w["phase"], w["attempt"])
                        counted[key] = counted.get(key, ln["carry_shots"] if w["phase"] == ln["start_phase"] else 0) + 1   # CMP 7.18: shots already fired
                        if counted[key] > p["shots"]:
                            why, code = f"more than {p['shots']} record shots in {p['label'].lower()}", "too_many"
                if why and s["id"] is not None:
                    out.append({"shot_id": s["id"], "device_id": ln["device_id"], "lane": ln["lane"], "n": s["series_no"],
                                "stream": s["stream"], "why": why, "event_ms": t, "decision": decisions.get(s["id"]),
                                "miss": bool(s["is_miss"]), "decimal": s["tenths"] / 10.0, "ring": s["ring"],
                                "guide": guidance(code, kind, s["stream"], rules_of(fmt))})
        return out

    def _clock(self, m, now: int) -> dict:
        fmt = json.loads(m["format_json"])
        i = m["phase"]
        p = fmt["phases"][i] if 0 <= i < len(fmt["phases"]) else None
        nxt = fmt["phases"][i + 1] if p and i + 1 < len(fmt["phases"]) else None
        rem = self.remaining_ms(m, now)
        prev = fmt["phases"][i - 1] if p and i > 0 else None
        if m["state"] == "overtime":                       # the match time is over; moved shooters are finishing
            return {"state": "overtime", "paced": m["paced"], "phase": i, "phase_count": len(fmt["phases"]),
                    "label": "Time over: moved shooters are finishing", "prev_end_say": "", "kind": None, "position": None,
                    "shots": 0, "seconds": 0, "extra_ms": 0, "remaining_ms": 0, "ends_at_ms": None, "next_start_say": "",
                    "next": None, "warnings": [], "start_say": "", "end_say": "", "server_time_ms": now,
                    "paused": m["phase_started_ms"] is None}
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
            lanes.append({**self.lane_clock(c, m, ln, now), "device_id": ln["device_id"], "lane": ln["lane"], "competitor": ln["competitor"], "pos": ln["final_pos"],
                          "target": (t["name"] if t and t["name"] else ln["device_id"]), "series_id": ln["series_id"],
                          "online": bool(t) and now - t["last_seen_ms"] <= self.core.offline_after_ms,
                          "sighters": counts["sighter"], "record": counts["scored"],
                          "in_record": self._switched(c, mid, m["phase"], ln["device_id"]) if m["phase"] >= 0 else False})
        return {"id": m["id"], "name": m["name"], "format": fmt, "state": m["state"], "created_by": m["created_by"],
                "competition_id": m["competition_id"], "relay": m["relay"], "official_ms": m["official_ms"], "official_by": m["official_by"],
                "results_posted_ms": m["results_posted_ms"], "protest_until_ms": m["protest_until_ms"],
                "created_ms": m["created_ms"], "started_ms": m["started_ms"], "ended_ms": m["ended_ms"],
                "record_shots": mf.record_shots(fmt), "clock": self._clock(m, now), "lanes": lanes,
                "move_extra_s": MOVE_EXTRA_S[rules_of(fmt)], "tie_rules": rules_of(fmt),
                "interrupted": m["state"] == "paused" and bool(json.loads((c.execute("SELECT data FROM match_log WHERE match_id=? AND kind='pause' "
                                                                                     "ORDER BY id DESC LIMIT 1", (mid,)).fetchone() or {"data": "{}"})["data"] or "{}").get("interruption")),
                "flags": self.flags(c, m) if m["state"] != "ready" else [],
                "corrections": self.core.corrections.list(c, mid), "open_corrections": self.core.corrections.open_count(c, mid),
                "open_items": self.open_items(c, m) if m["official_ms"] is None else [],
                "final": self._final_view(m)}

    LOG_TEXT = {"start": "Match started", "end": "Match finished", "abort": "Match stopped", "pause": "Clock paused", "resume": "Clock resumed",
                "restart": "Restarted", "extra": "Extra time for everyone", "pacing": "Commands changed", "move": "Shooter moved to another target",
                "lane_ready": "Moved shooter ready", "lane_extra": "Extra time for this shooter", "lane_stop": "Shooter's own time ended",
                "left_relay": "Sent to a later relay (CMP 7.18)", "shoot_off": "Shoot-off", "joins_relay": "Joins from an earlier relay (CMP 7.18)",
                "posted": "Preliminary results posted", "official": "Results made official", "reopen": "Official results reopened",
                "interrupt": "Shooter's time stopped", "interrupt_end": "Shooter's time runs again", "malfunction": "Malfunction",
                "late": "Late arrival joined", "sighters": "Sighters allowed"}

    WORDS = {"count": "counted", "annul": "annulled", "miss": "scored as a miss", "penalty2": "counted, 2 points off",
             "annul_penalty2": "annulled, 2 points off", "ignore": "no penalty", "penalty2_first": "2 points off the first record shot",
             "miss_first": "first record shot scored as a miss", "upheld": "upheld", "not_upheld": "not upheld", "withdrawn": "withdrawn",
             "justified": "justified", "not_justified": "not justified", "found": "found", "not_found": "not found (a miss)"}

    def incidents(self, mid: int) -> dict:
        """The Range Incident Report (ISSF 6.10.4 h, CMP 6.5, NRA Range Officer's Notation): every irregularity,
        decision, correction, move, interruption and extra time of the match, in time order, with who did it."""
        c = self.db.conn()
        m = self._row(c, mid)
        fmt = json.loads(m["format_json"])
        lanes = self._lanes(c, mid)
        lane_of, who_of = {}, {}
        for ln in lanes:
            for d in self._lane_devs(c, mid, ln["device_id"]):
                lane_of[d] = ln["lane"]
            who_of[ln["lane"]] = ln["competitor"] or ""
        label = lambda i: fmt["phases"][i]["label"] if i is not None and 0 <= i < len(fmt["phases"]) else ""
        rows = []
        for r in c.execute("SELECT * FROM match_log WHERE match_id=? ORDER BY id", (mid,)):
            k = r["kind"]
            if k not in self.LOG_TEXT:
                continue
            d = json.loads(r["data"]) if r["data"] else {}
            detail = {"restart": "back to " + label(r["phase"]), "extra": "+%s s" % d.get("seconds"), "lane_extra": "+%s s" % d.get("seconds"),
                      "pacing": d.get("paced", ""), "move": "from %s" % d.get("from", ""), "lane_ready": "+%s s extra" % d.get("extra_s"),
                      "official": "protest time overridden" if d.get("protest_time_overridden") else "",
                      "left_relay": "%s record shots fired, %s s left" % (d.get("shots"), (d.get("left_ms") or 0) // 1000)}.get(k, label(r["phase"]) if k in ("pause", "resume") else "")
            lane = lane_of.get(r["device_id"]) if r["device_id"] else None
            rows.append({"at_ms": r["at_ms"], "lane": lane, "competitor": who_of.get(lane, ""), "what": self.LOG_TEXT[k], "detail": detail,
                         "rule": "CMP 7.18" if k in ("left_relay", "joins_relay") else "", "by": d.get("by", "")})
        decided = {r["shot_id"]: r for r in c.execute("SELECT * FROM shot_decisions WHERE match_id=?", (mid,))}
        if m["state"] != "ready":
            for f in self.flags(c, m):
                dd = decided.get(f["shot_id"])
                rows.append({"at_ms": f["event_ms"], "lane": f["lane"], "competitor": who_of.get(f["lane"], ""),
                             "what": "Flagged %s shot %s (%s)" % ("record" if f["stream"] == "scored" else "sighting", f["n"], "miss" if f["miss"] else "%.1f" % f["decimal"]),
                             "detail": f["why"] + (" · decided: " + self.WORDS.get(f["decision"], f["decision"]) if f["decision"] else " · NOT DECIDED"),
                             "rule": (f["guide"] or {}).get("rule") or "", "by": dd["decided_by"] if dd else ""})
        for x in self.core.corrections.list(c, mid):
            d = x["data"]
            detail = "; ".join(v for v in (
                ("value %s" % ("miss" if d.get("miss") else "%.1f" % (d["tenths"] / 10))) if "tenths" in d and x["kind"] in ("override", "added", "not_registered") else "",
                ("from firing point %s" % d["from_lane"] if d.get("from_lane") else "source unknown") if x["kind"] == "crossfire" else "",
                ("-%s points" % d["points"]) if x["kind"] == "penalty" else "",
                ("second official " + d["official"]) if x["kind"] == "dq" else "",
                d.get("note") or "", ("outcome: " + self.WORDS.get(d["outcome"], d["outcome"])) if d.get("outcome") else "",
                ("settled by " + x["settled_by"]) if x["settled_by"] and x["status"] != "void" else "",
                "VOID (voided by %s)" % x["settled_by"] if x["status"] == "void" else "", "OPEN" if x["status"] == "open" else "") if v)
            shot = c.execute("SELECT series_no, stream FROM shots WHERE id=?", (x["shot_id"],)).fetchone() if x["shot_id"] else None
            rows.append({"at_ms": x["at_ms"], "lane": x["lane"], "competitor": who_of.get(x["lane"], ""),
                         "what": x["kind"].replace("_", " ").capitalize() + (" · %s shot %s" % ("record" if shot["stream"] == "scored" else "sighting", shot["series_no"]) if shot else ""),
                         "detail": detail, "rule": d.get("rule") or "", "by": x["by"] or ""})
        rows.sort(key=lambda r: r["at_ms"])
        return {"match_id": mid, "name": m["name"], "format_name": fmt["name"], "rulebook": rules_of(fmt), "started_ms": m["started_ms"],
                "ended_ms": m["ended_ms"], "official_ms": m["official_ms"], "rows": rows}

    def lane_clock(self, c, m, ln, now: int) -> dict:
        """A lane's own clock, for the console and the shooter's screen: own = "moving" (time stands still until the
        range officer presses Ready), "own" (finishing their phase after the match went on), "extra" (more time than the
        match in this phase) or "" (the match clock). moved_from: the targets they left, oldest first."""
        fmt = json.loads(m["format_json"])
        if ln["left_ms"] is not None or (ln["start_phase"] > m["phase"] and m["state"] in LIVE):   # CMP 7.18
            left = ln["left_ms"] is not None
            relay = c.execute("SELECT relay FROM matches WHERE id=?", (ln["to_match"] if left else ln["from_match"],)).fetchone()
            return {"own": "left" if left else "waiting", "own_label": fmt["phases"][ln["start_phase"]]["label"] if not left else None,
                    "own_kind": None, "own_remaining_ms": None, "own_ends_at_ms": None, "sighting": False, "moved_from": [],
                    "relay_note": ("finishes the match on relay %s" % relay["relay"] if left else "joins from relay %s at %s" % (relay["relay"], fmt["phases"][ln["start_phase"]]["label"])) if relay else None}
        own = ("moving" if ln["moving_since_ms"] is not None else "own" if ln["own_phase"] is not None
               else "extra" if ln["extra_ms"] and m["state"] in LIVE else "")
        phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
        left = self.lane_left_ms(m, ln, now)
        counting = (own == "own" and ln["own_since_ms"] is not None) or (own in ("extra", "") and m["state"] == "running")
        devs = self._lane_devs(c, m["id"], ln["device_id"])[1:]
        names = {r["device_id"]: r["name"] for r in c.execute(
            f"SELECT device_id, name FROM targets WHERE device_id IN ({','.join('?' * len(devs))})", devs)} if devs else {}
        return {"own": own, "stop": self._stop_reason(c, m["id"], ln["device_id"]) if own == "moving" else None,
                "own_label": fmt["phases"][phase]["label"] if own and 0 <= phase < len(fmt["phases"]) else None,
                "own_kind": fmt["phases"][phase]["kind"] if own and 0 <= phase < len(fmt["phases"]) else None,
                "own_remaining_ms": left if own else None, "own_ends_at_ms": now + left if own and counting else None,
                "sighting": bool(ln["sighting"]), "moved_from": [names.get(d) or d for d in reversed(devs)]}

    def _final_view(self, m) -> Optional[dict]:
        """A final's standings for its console (from the competition's results), or None for any other match."""
        if not m["final_kind"]:
            return None
        try:
            return next((f for f in self.core.competitions.results(m["competition_id"])["finals"] if f["match_id"] == m["id"]), None)
        except MatchError:
            return None

    def list(self, limit: int = 30) -> List[dict]:
        now = self.core.now_ms()
        out = []
        for m in self.db.conn().execute("SELECT * FROM matches ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 200)),)):
            fmt = json.loads(m["format_json"])
            out.append({"id": m["id"], "name": m["name"], "state": m["state"], "format_name": fmt["name"],
                        "created_ms": m["created_ms"], "clock": self._clock(m, now),
                        "competition_id": m["competition_id"], "relay": m["relay"], "results_hidden": bool(m["results_hidden"]),
                        "official_ms": m["official_ms"]})
        return out

    def target_info(self, c, device_id: str, now: int) -> Optional[dict]:
        """What a target's own screen shows during a match (no login needed: read only)."""
        r = c.execute("""SELECT m.*, l.lane, l.competitor FROM matches m JOIN match_lanes l ON l.match_id=m.id
                         WHERE l.device_id=? AND m.state IN """ + ACTIVE_SQL + " ORDER BY m.id DESC LIMIT 1",
                      (device_id,)).fetchone()
        if r is None:
            return None
        clock = self._clock(r, now)
        clock.update({"match_id": r["id"], "name": r["name"], "lane": r["lane"], "competitor": r["competitor"],
                      "in_record": self._switched(c, r["id"], r["phase"], device_id) if r["phase"] >= 0 else False})
        ln = self._lane(c, r["id"], device_id)
        lc = self.lane_clock(c, r, ln, now)
        if lc["own"] == "waiting":                      # CMP 7.18: rejoining at a later phase
            clock.update(state="paused", label="Waiting: you start at " + lc["own_label"], remaining_ms=0, ends_at_ms=None, kind=None)
        elif lc["own"] == "moving":
            clock.update(state="paused", label={"interrupt": "Interrupted", "malfunction": "Rifle being repaired"}.get(lc["stop"], "Moving to another target") + ": your time is stopped", remaining_ms=lc["own_remaining_ms"],
                         ends_at_ms=None, next=None)
        elif lc["own"] in ("own", "extra"):
            fmt = json.loads(r["format_json"])
            p = fmt["phases"][ln["own_phase"] if ln["own_phase"] is not None else r["phase"]]
            clock.update(state="running" if lc["own_ends_at_ms"] is not None else "paused", label=p["label"] + " (your own time)",
                         kind=p["kind"], shots=p["shots"], remaining_ms=lc["own_remaining_ms"], ends_at_ms=lc["own_ends_at_ms"],
                         next=None if lc["own"] == "own" else clock.get("next"))
        if lc["sighting"]:
            clock.update(in_record=False, sighting=True)
        clock["moved_from"] = lc["moved_from"]
        return clock

    # ------------------------------------------------------------------------------------------- results
    def counted(self, c, m, ln, flags: Dict[int, dict]) -> List[dict]:
        """One lane's counted record shots in firing order, after the match director's decisions and the corrections
        (corrections.py). A record shot counts unless flagged; flagged ones only as decided (count as fired, miss = 0,
        penalty2 = 2 points off, not below 0; annul / undecided do not count). Corrections: annulled shots (also
        crossfires received) are left out; an override replaces the value (orig keeps the fired one); added shots and
        crossfire misses take their place in firing order (n None). Decisions on stray sighting shots land on the first
        record shot (miss_first: 0; penalty2_first: 2 points off each); penalties on their shot, or
        on the first series. Values: tenths (decimal x10) and ring, both after penalties."""
        out = []
        if ln["series_id"] is None:
            return out
        eff = self.core.corrections.effects(c, m, ln["lane"])
        for s in c.execute("""SELECT id, is_miss, tenths, ring, ax_h, ay_h, series_no, event_ms FROM shots
                              WHERE series_id=? AND stream='scored' ORDER BY event_ms, id""", (ln["series_id"],)):
            if s["id"] in eff["annulled"]:
                continue
            f = flags.get(s["id"])
            decision = f["decision"] if f else "count"
            if decision not in ("count", "miss", "penalty2"):
                continue
            fired = {"tenths": s["tenths"], "ring": s["ring"], "miss": bool(s["is_miss"]), "inner": not s["is_miss"] and is_inner(s["tenths"])}
            o = eff["overrides"].get(s["id"])
            v = {k: o[k] for k in ("tenths", "ring", "miss", "inner")} if o else fired
            miss = v["miss"] or decision == "miss"
            tenths, ring = (0, 0) if miss else (v["tenths"], v["ring"])
            if decision == "penalty2":
                tenths, ring = max(0, tenths - 20), max(0, ring - 2)
            out.append({"id": s["id"], "n": s["series_no"], "tenths": tenths, "ring": ring, "miss": miss,
                        "inner": decision == "count" and not miss and v["inner"], "decision": decision if f else None,
                        "x_mm": None if s["is_miss"] else s["ax_h"] / 100.0, "y_mm": None if s["is_miss"] else s["ay_h"] / 100.0,
                        "t": s["event_ms"], "orig": fired if o else None, "override": o["note"] if o else None})
        for a in eff["added"]:
            out.append({"id": None, "n": None, "tenths": 0 if a["miss"] else a["tenths"], "ring": 0 if a["miss"] else a["ring"], "miss": a["miss"],
                        "inner": a["inner"], "decision": None, "x_mm": None, "y_mm": None, "t": a["at_ms"],
                        "added": not a.get("crossfire_to"), "crossfire_to": a.get("crossfire_to"), "note": a.get("note")})
        out.sort(key=lambda x: (x["t"], x["id"] or 0))
        pens = [f["decision"] for f in flags.values() if f["device_id"] == ln["device_id"] and f["stream"] == "sighter"]
        if out and ("miss_first" in pens or "penalty2_first" in pens):
            first = out[0]
            if "miss_first" in pens:
                first.update(tenths=0, ring=0, inner=False)
            n = pens.count("penalty2_first")
            first.update(tenths=max(0, first["tenths"] - 20 * n), ring=max(0, first["ring"] - 2 * n), inner=first["inner"] and not n,
                         decision=first["decision"] or ("miss_first" if "miss_first" in pens else "penalty2_first"))   # shown as decided
        for p in self._decision_penalties(m, ln, flags):   # too many shots: 2 points off the LOWEST shot of the first series
            for x in sorted(out[:10], key=lambda y: (y["tenths"], y["t"]))[:1]:
                x.update(tenths=max(0, x["tenths"] - 20), ring=max(0, x["ring"] - 2), inner=False)
                x.setdefault("penalties", []).append(p["rule"])
        for p in eff["penalties"]:                     # whole points off its shot, or off the first series (its best shot,
            if p["shot_id"] is not None:                # so the whole deduction applies: ISSF 6.11.6.3, 6.12.6.1 a)
                hit = [y for y in out if y["id"] == p["shot_id"]]
            else:
                hit = sorted(out[:10], key=lambda y: -y["tenths"])[:1]
            for x in hit:
                x.update(tenths=max(0, x["tenths"] - 10 * p["points"]), ring=max(0, x["ring"] - p["points"]), inner=False)
                x.setdefault("penalties", []).append(p["rule"])
        return out

    @staticmethod
    def _decision_penalties(m, ln, flags: Dict[int, dict]) -> List[dict]:
        """2 points for each record shot the director annulled as one too many (decision annul_penalty2)."""
        rule = TOO_MANY_RULE.get(rules_of(json.loads(m["format_json"])), "too many shots")
        return [{"points": 2, "rule": rule, "note": "shot %s: one too many" % f["n"], "shot_id": None}
                for f in flags.values() if f["device_id"] == ln["device_id"] and f["stream"] == "scored" and f["decision"] == "annul_penalty2"]

    def counted_row(self, c, m, ln, flags: Dict[int, dict]) -> tuple:
        """(counted shots, extras) for a results row. Disqualified: no shots (their values are cleared). extras: dq,
        penalties ({points, rule, note}) and removed (record shots annulled by a correction, for the score card)."""
        shots = self.counted(c, m, ln, flags)
        eff = self.core.corrections.effects(c, m, ln["lane"])
        removed = []
        for sid, a in eff["annulled"].items():
            r = c.execute("SELECT series_no, stream, tenths, is_miss FROM shots WHERE id=?", (sid,)).fetchone()
            if r is not None and r["stream"] == "scored":
                why = ("crossfire from firing point %d" % a["from_lane"] if a["from_lane"] else "crossfire (source unknown)") \
                    if a["kind"] == "crossfire" else (a["note"] or "annulled")
                removed.append({"n": r["series_no"], "tenths": 0 if r["is_miss"] else r["tenths"], "why": why})
        book = rules_of(json.loads(m["format_json"]))
        dq = dict(eff["dq"], text=lookup_text(eff["dq"]["rule"], book)) if eff["dq"] else None
        extras = {"dq": dq, "removed": sorted(removed, key=lambda x: x["n"] or 0),
                  "penalties": [{"points": p["points"], "rule": p["rule"], "note": p["note"], "text": lookup_text(p["rule"], book)}
                                for p in self._decision_penalties(m, ln, flags) + eff["penalties"]]}
        return ([] if eff["dq"] else shots), extras

    @staticmethod
    def entry_view(shots: List[dict], decimal: bool) -> dict:
        """What a results row shows, from counted shots."""
        vals = [s["tenths"] if decimal else s["ring"] * 10 for s in shots]
        ser = [sum(vals[i:i + 10]) for i in range(0, len(vals), 10)]
        return {"total": sum(vals) / 10.0, "x": sum(1 for s in shots if s["inner"]), "shots": len(shots),
                "series": [v / 10.0 for v in ser], "last10": [v / 10.0 for v in vals[-10:]]}

    def results(self, mid: int) -> dict:
        """Standings of one match, ranked with its format's tie-break rules (ranking.py)."""
        c = self.db.conn()
        m = self._row(c, mid)
        fmt = json.loads(m["format_json"])
        decimal, rules = fmt.get("scoring") == "decimal", rules_of(fmt)
        flags = {f["shot_id"]: f for f in (self.flags(c, m) if m["state"] != "ready" else [])}
        entries = []
        for ln in self._lanes(c, mid):
            shots, x = self.counted_row(c, m, ln, flags)
            t = c.execute("SELECT name FROM targets WHERE device_id=?", (ln["device_id"],)).fetchone()
            name = ln["competitor"] or ""
            entries.append(dict(self.entry_view(shots, decimal), summary=ranking.summary(shots), sort_name=(name.split() or [""])[-1], **x,
                                lane=ln["lane"], device_id=ln["device_id"], competitor=name,
                                target=(t["name"] if t and t["name"] else ln["device_id"]),
                                pending=sum(1 for f in flags.values() if f["device_id"] == ln["device_id"] and not f["decision"])))
        rows = ranking.rank(entries, rules, decimal)
        for r in rows:
            r.pop("summary", None)
        return {"id": mid, "name": m["name"], "format_name": fmt["name"], "scoring": fmt.get("scoring"), "tie_rules": rules,
                "record_shots": mf.record_shots(fmt), "state": m["state"], "clock": self._clock(m, self.core.now_ms()),
                "provisional_ranking": False, "rows": rows, "official_ms": m["official_ms"], "official_by": m["official_by"],
                "results_posted_ms": m["results_posted_ms"], "protest_until_ms": m["protest_until_ms"]}

    def board(self, ids: Optional[List[int]] = None) -> List[dict]:
        """Public scoreboard data for the given matches, or for every active match and those finished in the last 6 h."""
        c = self.db.conn()
        if not ids:
            since = self.core.now_ms() - 6 * 3600 * 1000
            ids = [r["id"] for r in c.execute("SELECT id FROM matches WHERE results_hidden=0 AND (state IN ('running','paused','holding','ready','overtime') "
                                               "OR (state='done' AND ended_ms>?)) ORDER BY id", (since,))]
        else:                                          # deleted results are not shown, even when asked for
            ids = [i for i in ids if c.execute("SELECT 1 FROM matches WHERE id=? AND results_hidden=0", (int(i),)).fetchone()]
        out = []
        for mid in ids[:20]:
            try:
                out.append(self.results(int(mid)))
            except (MatchError, ValueError):
                continue
        return out


def rules_of(fmt: dict) -> str:
    """A format's tie-break rules (older stored formats have none: use their rulebook's, ISSF for custom ones)."""
    r = fmt.get("tie_rules") or {"ISSF": "ISSF", "CMP": "CMP", "NRA": "NRA"}.get(fmt.get("rulebook"), "ISSF")
    return r if r in ranking.RULES else "ISSF"


def is_inner(tenths: int) -> bool:
    from .scoring import is_inner_ten
    return is_inner_ten(tenths)


def own_clock(ln) -> bool:
    """A lane that does not simply follow the match: moving, finishing its own phase, or sighting after a move."""
    return ln["moving_since_ms"] is not None or ln["own_phase"] is not None or bool(ln["sighting"])


def _pauses(log) -> List[dict]:
    """Every time the match clock was paused: [start, end) (end None = still paused)."""
    out, cur = [], None
    for r in log:
        if r["kind"] == "pause" and cur is None:
            cur = {"start": r["at_ms"], "end": None}
        elif r["kind"] in ("resume", "restart", "abort") and cur is not None:
            cur["end"] = r["at_ms"]
            out.append(cur)
            cur = None
    return out + ([cur] if cur else [])


def _lane_spans(log, devs, wins):
    """A lane's own timeline from the log: moving spans (its time stood still) and own phases (it finished a phase
    after the match went on), each [start, end) with end None while open. A restart or abort ends both."""
    moving, own, mv, ow, restarts = [], [], None, None, []
    for r in log:
        k, mine = r["kind"], r["device_id"] in devs
        if k in ("move", "interrupt") and mine and r["phase"] is not None and mv is None:
            mv = {"start": r["at_ms"], "end": None, "why": "while moving to another target" if k == "move" else
                  "during an interruption" if json.loads(r["data"]).get("reason") == "interrupt" else "while the rifle was being repaired"}
        elif k in ("lane_ready", "interrupt_end") and mine and mv is not None:
            mv["end"] = r["at_ms"]
            moving.append(mv)
            mv = None
        elif k == "lane_overtime" and mine and ow is None:
            attempt = max((w["attempt"] for w in wins if w["phase"] == r["phase"] and w["start_id"] < r["id"]), default=1)
            ow = {"phase": r["phase"], "attempt": attempt, "start": r["at_ms"], "end": None, "start_id": r["id"]}
        elif (k == "lane_end" and mine) or k in ("restart", "abort"):
            if k == "restart":
                restarts.append((r["phase"], r["id"]))
            for sp in (mv, ow):
                if sp is not None:
                    sp["end"] = r["at_ms"]
            if mv is not None:
                moving.append(mv)
            if ow is not None:
                own.append(ow)
            mv = ow = None
    moving += [mv] if mv else []
    own += [ow] if ow else []
    for o in own:
        o["superseded"] = any(rid > o["start_id"] and to <= o["phase"] for to, rid in restarts)
    return moving, own


def interruption_time(book: str, reason: str, left_ms: int, lost_s: int) -> tuple:
    """(time left, sighters?, rule) for a shooter whose interruption (or NRA disabled rifle) ended after lost_s seconds;
    left_ms: what they had left when it began (their clock stood still since).
    ISSF 6.11.3.1: over 3 minutes, the time lost (in the last 5 minutes: the time left + 1 minute); up to 3 minutes the
    time lost is not given back; 6.11.3.2: over 5 minutes, + 5 minutes and unlimited sighters.
    CMP 7.18.1-2: the time lost; over 5 minutes + 2 minutes and unlimited sighters.
    NRA 9.2 b: over 3 minutes equal time, over 5 unlimited sighters; 9.5 (disabled rifle): the time that remained, sighters."""
    if reason == "malfunction":                              # only NRA stops the clock for a malfunction
        return left_ms, True, "NRA 9.5"
    if book == "CMP":
        return (left_ms + 120_000, True, "CMP 7.18.2") if lost_s > 300 else (left_ms, False, "CMP 7.18.1")
    if book == "NRA":
        back = left_ms if lost_s > 180 else max(0, left_ms - lost_s * 1000)
        return back, lost_s > 300, "NRA 9.2 b"
    if lost_s <= 180:
        return max(0, left_ms - lost_s * 1000), False, "ISSF 6.11.3.1"
    back = left_ms + (60_000 if left_ms <= 300_000 else 0)
    return (back + 300_000, True, "ISSF 6.11.3.2") if lost_s > 300 else (back, False, "ISSF 6.11.3.1")


def range_interruption(book: str, lost_s: int) -> tuple:
    """(extra seconds, sighters?, rule) for the whole range after an interruption of lost_s seconds (the clock was
    stopped, so the time lost is given back): ISSF 6.10.9.1 / 6.11.3.2 over 5 minutes + 5 minutes and sighters;
    CMP 7.18.4 over 5 minutes + 2 minutes and sighters; NRA 9.2 b over 5 minutes sighters."""
    if lost_s <= 300:
        return 0, False, {"ISSF": "ISSF 6.11.3.1", "CMP": "CMP 7.18.4", "NRA": "NRA 9.2 b"}[book]
    return {"ISSF": (300, True, "ISSF 6.10.9.1, 6.11.3.2"), "CMP": (120, True, "CMP 7.18.4"), "NRA": (0, True, "NRA 9.2 b")}[book]
