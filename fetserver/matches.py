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

Moving a shooter to another target during a match (ISSF 6.10.9.2 / 6.11.3.2, CMP AG 7.12.2, CMP 3PAR 7.18.3, NRA 10.10.5 c): the
lane's series goes with them to the new target (their shots so far stay theirs), their own time stands still from the
move until the range officer says they are ready, they then get their rulebook's extra time (rulebooks.move_extra: ISSF and NRA 5 min,
the CMP books 2 min, none without a rulebook) and unlimited sighters until they start record again.
When the match's phase ends before their own time does, they finish it on their own clock ("own" phase) while the
match goes on; a match whose time is over while someone is still finishing is in state 'overtime'.

Shots are never dropped or changed here: a shot that the timeline says was out of time (or one record shot too many)
is FLAGGED, and does not count until the match director decides (count / annul / miss / -2 points).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from fetcore import faces
from fetcore import matchengine as me
from fetcore.matchrun import Run, not_official
from fetcore import matchformats as mf
from fetcore import ranking
from fetcore import rulebooks as rb
from fetcore.rulebooks import interruption_time, range_interruption   # noqa: F401 (tests and callers use them from here)
from .admin import ROLES, USERNAME_RE, MIN_PASSWORD, hash_password

from fetcore.matchengine import (ACTIVE, ACTIVE_SQL, CLOSE_MS, DECISIONS, LIVE, MAX_LANES, PACING,   # noqa: F401 (callers
                                 SIGHTER_DECISIONS, UNPLACED_DECISIONS, MatchError, _check, own_clock, rules_of)   # import them from here)
from fetcore.matchengine import discharged_at as _discharged_at


class _Facts:
    """What a Run reads besides the match's own rows, loaded when it asks (a tick seldom needs the shots)."""
    def __init__(self, c, mid):
        self.c, self.mid, self._shots, self._unplaced = c, mid, {}, {}
        self._decisions = self._corrections = None

    def shots(self, series_id):
        if series_id not in self._shots:
            self._shots[series_id] = Matches._series_shots(self.c, series_id)
        return self._shots[series_id]

    def unplaced(self, series_id):
        if series_id not in self._unplaced:
            self._unplaced[series_id] = Matches._series_unplaced(self.c, series_id)
        return self._unplaced[series_id]

    def decisions(self):
        if self._decisions is None:
            self._decisions = {r["shot_id"]: r["decision"] for r in self.c.execute("SELECT shot_id, decision FROM shot_decisions WHERE match_id=?", (self.mid,))}
        return self._decisions

    def corrections(self):
        if self._corrections is None:
            self._corrections = self.c.execute("SELECT * FROM shot_corrections WHERE match_id=? AND status!='void' ORDER BY id", (self.mid,)).fetchall()
        return self._corrections


class Matches:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.close_ms: Optional[int] = CLOSE_MS        # None: no "too soon after another report" flags (some tests)

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
        """Every format, with its rulebook (custom formats saved before rulebooks were named: NONE, until edited)."""
        out = [dict(f, builtin=True) for f in mf.builtin_list()]
        for r in self.db.conn().execute("SELECT json FROM match_formats ORDER BY id"):
            out.append(dict(json.loads(r["json"]), builtin=False))
        return [dict(f, book=rb.book_of(f), book_name=rb.BOOKS[rb.book_of(f)]["name"], book_named="book" in f) for f in out]

    def format(self, fid: str) -> Optional[dict]:
        if fid in mf.BUILTIN:
            return mf.validate(mf.BUILTIN[fid])
        r = self.db.conn().execute("SELECT json FROM match_formats WHERE id=?", (fid,)).fetchone()
        return json.loads(r["json"]) if r else None

    def save_format(self, fmt, by: str) -> dict:
        try:
            if not isinstance(fmt, dict) or fmt.get("book") not in rb.BOOKS:
                raise ValueError("choose the format's rulebook (or No rulebook): " + ", ".join(rb.BOOKS))
            clean = mf.validate(dict(fmt, rulebook="Custom"))
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
        (e.g. the standard one with a longer first sighting period, CMP 3PAR 5.3.1 d); it is marked as modified."""
        if not isinstance(name, str) or not name.strip():
            raise MatchError("the match needs a name")
        base = fmt if fmt is not None else (self.format(fmt_id) if isinstance(fmt_id, str) else None)   # fmt: a competition's own
        if base is None:
            raise MatchError("unknown format")
        fmt = base
        if override is not None:
            try:
                fmt = mf.validate(dict(override, id=base["id"], rulebook=base["rulebook"], book=rb.book_of(base), edition=base.get("edition", "")))
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
                self.core.wake_in(c, ln["device_id"])     # a sleeping target ignores shots: never in a match
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

    # The commands run in fetcore.matchrun.Run (no database): load the match, run the command, write what changed.
    def _run(self, c, mid) -> Run:
        return Run(self._row(c, mid), self._lanes(c, mid), self._log_rows(c, mid), _Facts(c, mid), self.close_ms)

    def _apply(self, c, run: Run) -> None:
        """Write what a command changed, in its transaction: the match row, the lanes, the new log rows (in order: their
        ids keep the log's order), then the target switches in the order the engine made them."""
        mid = run.m["id"]
        ch = run.match_changes()
        if ch:
            c.execute("UPDATE matches SET " + ", ".join(f"{k}=?" for k in ch) + " WHERE id=?", (*ch.values(), mid))
        for dev, cols in run.lane_changes():
            c.execute("UPDATE match_lanes SET " + ", ".join(f"{k}=?" for k in cols) + " WHERE match_id=? AND device_id=?",
                      (*cols.values(), mid, dev))
        for r in run.new_log:
            c.execute("INSERT INTO match_log (match_id, at_ms, kind, phase, device_id, data) VALUES (?,?,?,?,?,?)",
                      (mid, r["at_ms"], r["kind"], r["phase"], r["device_id"], r["data"]))
        for did, mode, session, at in run.modes:
            self.core._set_mode_in(c, did, mode, session, at)

    def _set_modes(self, c, m, mode: str, now: int, only: Optional[str] = None, everyone: bool = False) -> None:
        run = self._run(c, m["id"])
        run.set_modes(m, mode, now, only=only, everyone=everyone)
        self._apply(c, run)

    def _position_view(self, c, m, ln, fmt) -> dict:
        return self._run(c, m["id"]).position_view(m, ln, fmt)

    def _position_state(self, c, mid, ln, phase) -> tuple:
        return self._run(c, mid).position_state(ln, phase)

    def start(self, mid: int, by: str) -> None:
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self._row(c, mid)
            if m["state"] != "ready":
                raise MatchError("only a match that has not started can be started")
            fmt = json.loads(m["format_json"])
            for ln in self._lanes(c, mid):        # a fresh match series per lane, sized to the format
                sid = self.core._set_mode_in(c, ln["device_id"], "sighter", ln["competitor"] or m["name"], now, new_series=True)
                c.execute("UPDATE series SET total=? WHERE id=?", (mf.record_shots(fmt), sid))
                c.execute("UPDATE match_lanes SET series_id=? WHERE match_id=? AND device_id=?", (sid, mid, ln["device_id"]))
            run = self._run(c, mid)
            run.start(by, now)
            self._apply(c, run)
        self._pub(mid)

    def _done(self, c, m, now: int) -> None:
        run = self._run(c, m["id"])
        run.done(m, now)
        self._apply(c, run)

    def remaining_ms(self, m, now: int) -> int:
        return me.remaining_ms(json.loads(m["format_json"]), m, now)

    def tick(self) -> int:
        """End every phase whose time is up. Returns how many phases ended (tests use it)."""
        now = self.core.now_ms()
        ended = 0
        ids = [r["id"] for r in self.db.conn().execute(
            "SELECT id FROM matches WHERE state IN ('running','overtime') OR id IN (SELECT match_id FROM match_lanes WHERE own_since_ms IS NOT NULL)")]   # (running covers lanes with less time)
        for mid in ids:
            with self.db.write() as c:
                run = self._run(c, mid)
                n = run.tick(now)
                self._apply(c, run)
            if n:
                self._pub(mid)
            ended += n
        return ended

    def final_scores(self, c, m) -> Dict[int, dict]:
        return self._run(c, m["id"]).final_scores(m)

    _countback_3p = staticmethod(me.countback_3p)

    def shoot_off(self, mid: int, lanes: List[int], by: str) -> None:
        """A tie-breaking shot for the tied firing points of a final (fetcore.matchrun.Run.shoot_off)."""
        now = self.core.now_ms()
        with self.db.write() as c:
            run = self._run(c, mid)
            run.shoot_off(lanes, by, now)
            self._apply(c, run)
        self._pub(mid)

    def _overrun(self, m, now: int) -> int:
        return me.overrun(json.loads(m["format_json"]), m, now)

    def command(self, mid: int, action: str, by: str, seconds: Optional[int] = None, device_id: Optional[str] = None,
                paced: Optional[str] = None) -> None:
        """Range officer actions (fetcore.matchrun.Run.command): go, pause, resume, next, extra, abort, interrupt,
        interrupt_end, pacing, discharge; start_record and back_to_sighters are one lane's (lane_command). restart is
        separate because only a director may do it."""
        if action in ("start_record", "back_to_sighters"):
            return self.lane_command(mid, device_id, action, by)
        now = self.core.now_ms()
        with self.db.write() as c:
            run = self._run(c, mid)
            run.command(action, by, now, seconds=seconds, paced=paced)
            self._apply(c, run)
        self._pub(mid)

    def restart(self, mid: int, phase: int, by: str) -> None:
        """Go back to the start of `phase` (fetcore.matchrun.Run.restart)."""
        now = self.core.now_ms()
        with self.db.write() as c:
            run = self._run(c, mid)
            run.restart(phase, by, now)
            self._apply(c, run)
        self._pub(mid)

    def _switched(self, c, mid, phase, device_id) -> bool:
        return self._run(c, mid).switched(phase, device_id)

    @staticmethod
    def _lane_devs(c, mid, device_id) -> List[str]:
        """Every target a lane has used, newest first (a shooter may have been moved)."""
        return me.lane_devs(c.execute("SELECT kind, device_id, data FROM match_log WHERE match_id=? AND kind='move' ORDER BY id",
                                      (mid,)).fetchall(), device_id)

    # ------------------------------------------------------------------------------------------- moving a shooter
    def lane_left_ms(self, m, ln, now: int) -> int:
        """A lane's own time left in its phase."""
        return me.lane_left_ms(json.loads(m["format_json"]), m, ln, now)

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
            run = self._run(c, mid)
            ln = run.move_check(from_dev)
            if to_dev == from_dev:
                raise MatchError("choose another target")
            if not isinstance(to_dev, str) or c.execute("SELECT 1 FROM targets WHERE device_id=? AND removed=0 AND banned=0",
                                                        (to_dev,)).fetchone() is None:
                raise MatchError("unknown target")
            other = self.lock_of(c, to_dev)
            if other:
                raise MatchError(f"that target is in the match '{other}'")
            run.move(from_dev, to_dev, by, now)
            if run.m["state"] != "ready":
                self.core._move_series(c, from_dev, to_dev, ln["series_id"], now)
            self._apply(c, run)
            self.core.wake_in(c, to_dev)
        self._pub(mid)

    def add_late(self, mid: int, device_id: str, competitor: Optional[str], by: str, competitor_id: Optional[int] = None) -> None:
        """A shooter who arrives late joins the running match on a free target, with no extra time (ISSF 6.11.4,
        CMP AG 7.14, CMP 3PAR 7.20); after the preparation and sighting time they may still fire sighters, within their time."""
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
            p, late = fmt["phases"][m["phase"]], rb.rule(rb.book_of(fmt), "late")
            lane = (c.execute("SELECT MAX(lane) FROM match_lanes WHERE match_id=?", (mid,)).fetchone()[0] or 0) + 1
            name = competitor.strip()[:60] if isinstance(competitor, str) and competitor.strip() else None
            c.execute("INSERT INTO match_lanes (match_id, device_id, lane, competitor, competitor_id) VALUES (?,?,?,?,?)",
                      (mid, device_id, lane, name, competitor_id))
            self.core.wake_in(c, device_id)
            ln = self._lane(c, mid, device_id)
            sighting = p["kind"] in ("record", "stage")
            sid = self.core._set_mode_in(c, device_id, "sighter", Run._session(m, ln), now, new_series=True)
            c.execute("UPDATE series SET total=? WHERE id=?", (mf.record_shots(fmt), sid))
            c.execute("UPDATE match_lanes SET series_id=?, sighting=? WHERE match_id=? AND device_id=?", (sid, 1 if sighting else 0, mid, device_id))
            self._log(c, mid, now, "late", m["phase"], device_id, {"competitor": name, "by": by, "rule": late})
            if sighting:
                self._log(c, mid, now, "sighters", m["phase"], device_id, {"rule": late})
        self._pub(mid)

    def _stop_reason(self, c, mid, device_id) -> Optional[str]:
        return self._run(c, mid).stop_reason(device_id)

    def lane_command(self, mid: int, device_id: str, action: str, by: str, seconds: Optional[int] = None) -> None:
        """One lane (fetcore.matchrun.Run.lane_command): ready, extra, start_record, back_to_sighters, interrupt,
        malfunction, interrupt_end."""
        now = self.core.now_ms()
        with self.db.write() as c:
            run = self._run(c, mid)
            run.lane_command(device_id, action, by, now, seconds=seconds)
            self._apply(c, run)
        self._pub(mid)

    # ------------------------------------------------------------------------------------------- official results
    not_official = staticmethod(not_official)

    def open_items(self, c, m) -> List[str]:
        """What stops the results from being made official, in words (empty: nothing)."""
        out = []
        if m["state"] != "done":
            out.append("the match has not finished" if m["state"] != "aborted" else "the match was stopped")
        undecided = sum(1 for f in self.flags(c, m) if not f["decision"]) if m["state"] != "ready" else 0
        if undecided:
            out.append(f"{undecided} flagged shot{'s' if undecided != 1 else ''} not decided")
        heard = sum(1 for u in self.unplaced(c, m) if not u["decision"]) if m["state"] != "ready" else 0
        if heard:
            out.append(f"{heard} shot{'s' if heard != 1 else ''} heard but not placed, not decided")
        n = self.core.corrections.open_count(c, m["id"])
        if n:
            out.append(f"{n} review{'s' if n != 1 else ''} or protest{'s' if n != 1 else ''} not settled")
        return out

    def protest_minutes(self, c, m) -> int:
        """The protest time after preliminary results: 10 minutes (ISSF 6.16.5.1, CMP AG 8.1.9, CMP 3PAR 8.2); NRA (16.1 b)
        and no rulebook: the challenge time set per competition, 30 minutes if not set."""
        mins = rb.protest_minutes(rb.book_of(json.loads(m["format_json"])))
        if mins is not None:
            return mins
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
        """The timeline as intervals (fetcore.matchengine.windows)."""
        return me.windows(self._log_rows(c, mid))

    @staticmethod
    def discharged_at(c, mid: int) -> Optional[int]:
        """When the range officer said "You may discharge air downrange" (docs/PROTOCOL.md, version 2): a shot fired
        from then on never counts and is never flagged (air discharged, a pellet left in the bore). A restart after it
        reopens the match, and the command no longer holds."""
        return _discharged_at(c.execute("SELECT kind, at_ms FROM match_log WHERE match_id=? AND kind IN ('discharge', 'restart') "
                                        "ORDER BY id", (mid,)).fetchall())

    # The facts the pure engine (fetcore.matchengine) works from, loaded from the database.
    @staticmethod
    def _log_rows(c, mid) -> list:
        return c.execute("SELECT * FROM match_log WHERE match_id=? ORDER BY id", (mid,)).fetchall()

    @staticmethod
    def _series_shots(c, series_id) -> list:
        if series_id is None:
            return []
        return c.execute("SELECT id, event_ms, stream, series_no, is_miss, tenths, ring, inner, ax_h, ay_h, misfit_um FROM shots "
                         "WHERE series_id=? ORDER BY event_ms, id", (series_id,)).fetchall()

    @staticmethod
    def _series_unplaced(c, series_id) -> list:
        if series_id is None:
            return []
        return c.execute("SELECT * FROM unplaced WHERE series_id=? ORDER BY event_ms, id", (series_id,)).fetchall()

    def flags(self, c, m) -> List[dict]:
        """Every shot of the match's lanes that the timeline says did not happen in its time, or is a record shot
        beyond the stage's count (fetcore.matchengine.flags)."""
        lanes = self._lanes(c, m["id"])
        decisions = {r["shot_id"]: r["decision"] for r in c.execute("SELECT * FROM shot_decisions WHERE match_id=?", (m["id"],))}
        return me.flags(json.loads(m["format_json"]), self._log_rows(c, m["id"]), lanes,
                        {ln["series_id"]: self._series_shots(c, ln["series_id"]) for ln in lanes},
                        {ln["series_id"]: self._series_unplaced(c, ln["series_id"]) for ln in lanes}, decisions,
                        {ln["lane"]: self.core.corrections.effects(c, m, ln["lane"]) for ln in lanes}, self.close_ms)

    def unplaced(self, c, m) -> List[dict]:
        """Record-time reports the target heard but could not place, on this match's firing points. They count as
        nothing until the director decides: ignore (noise, an echo) or miss (it was a shot)."""
        lanes = self._lanes(c, m["id"])
        return me.unplaced_reports(lanes, {ln["series_id"]: self._series_shots(c, ln["series_id"]) for ln in lanes},
                                   {ln["series_id"]: self._series_unplaced(c, ln["series_id"]) for ln in lanes},
                                   self.discharged_at(c, m["id"]), self.close_ms)

    def decide_unplaced(self, mid: int, unplaced_id: int, decision: str, by: str) -> None:
        if decision not in UNPLACED_DECISIONS and decision != "undecided":
            raise MatchError(f"decision must be one of {', '.join(UNPLACED_DECISIONS)}")
        with self.db.write() as c:
            m = self._row(c, mid)
            self.not_official(m)
            if not any(u["id"] == unplaced_id for u in self.unplaced(c, m)):
                raise MatchError("that report is not in this match")
            c.execute("UPDATE unplaced SET decision=?, decided_by=?, decided_ms=? WHERE id=?",
                      (None, None, None, unplaced_id) if decision == "undecided" else (decision, by, self.core.now_ms(), unplaced_id))
        self._pub(mid)

    def _clock(self, m, now: int) -> dict:
        return me.clock(json.loads(m["format_json"]), m, now)

    def detail(self, mid: int) -> dict:
        now = self.core.now_ms()
        c = self.db.conn()
        m = self._row(c, mid)
        fmt = json.loads(m["format_json"])
        run = self._run(c, mid)                       # the log, read once for every lane
        lanes = []
        for ln in self._lanes(c, mid):
            t = c.execute("SELECT name, last_seen_ms, face_id FROM targets WHERE device_id=?", (ln["device_id"],)).fetchone()
            counts = {"sighter": 0, "scored": 0}
            if ln["series_id"] is not None:
                for r in c.execute("SELECT stream, COUNT(*) AS n FROM shots WHERE series_id=? GROUP BY stream", (ln["series_id"],)):
                    counts[r["stream"]] = r["n"]
            lanes.append({**self.lane_clock(c, m, ln, now, run), "device_id": ln["device_id"], "lane": ln["lane"], "competitor": ln["competitor"], "pos": ln["final_pos"],
                          "target": (t["name"] if t and t["name"] else ln["device_id"]), "series_id": ln["series_id"],
                          "online": bool(t) and now - t["last_seen_ms"] <= self.core.offline_after_ms,
                          "sighters": counts["sighter"], "record": counts["scored"],
                          "in_record": run.switched(m["phase"], ln["device_id"]) if m["phase"] >= 0 else False,
                          "installed_face": faces.face(t["face_id"] if t else None).id,    # the mask on it (Range Control)
                          **run.position_view(m, ln, fmt)})
        return {"id": m["id"], "name": m["name"], "format": fmt, "face": faces.view(faces.face(fmt.get("face"))), "state": m["state"], "created_by": m["created_by"],
                "competition_id": m["competition_id"], "relay": m["relay"], "official_ms": m["official_ms"], "official_by": m["official_by"],
                "results_posted_ms": m["results_posted_ms"], "protest_until_ms": m["protest_until_ms"],
                "discharged_ms": self.discharged_at(c, m["id"]),
                "created_ms": m["created_ms"], "started_ms": m["started_ms"], "ended_ms": m["ended_ms"],
                "record_shots": mf.record_shots(fmt), "clock": self._clock(m, now), "lanes": lanes,
                "move_extra_s": rb.move_extra(rb.book_of(fmt))[0], "tie_rules": rules_of(fmt), "rb": rb.texts(rb.book_of(fmt)),
                "interrupted": m["state"] == "paused" and bool(json.loads((c.execute("SELECT data FROM match_log WHERE match_id=? AND kind='pause' "
                                                                                     "ORDER BY id DESC LIMIT 1", (mid,)).fetchone() or {"data": "{}"})["data"] or "{}").get("interruption")),
                "flags": self.flags(c, m) if m["state"] != "ready" else [],
                "unplaced": self.unplaced(c, m) if m["state"] != "ready" else [],
                "corrections": self.core.corrections.list(c, mid), "open_corrections": self.core.corrections.open_count(c, mid),
                "open_items": self.open_items(c, m) if m["official_ms"] is None else [],
                "final": self._final_view(m)}

    LOG_TEXT = {"start": "Match started", "end": "Match finished", "abort": "Match stopped", "pause": "Clock paused", "resume": "Clock resumed",
                "restart": "Restarted", "extra": "Extra time for everyone", "pacing": "Commands changed", "move": "Shooter moved to another target",
                "lane_ready": "Moved shooter ready", "lane_extra": "Extra time for this shooter", "lane_stop": "Shooter's own time ended",
                "left_relay": "Sent to a later relay", "shoot_off": "Shoot-off", "place": "Place decided", "joins_relay": "Joins from an earlier relay",
                "posted": "Preliminary results posted", "official": "Results made official", "reopen": "Official results reopened",
                "interrupt": "Shooter's time stopped", "interrupt_end": "Shooter's time runs again", "malfunction": "Malfunction",
                "late": "Late arrival joined", "sighters": "Sighters allowed"}

    WORDS = {"count": "counted", "annul": "annulled", "miss": "scored as a miss", "penalty2": "counted, 2 points off",
             "annul_penalty2": "annulled, 2 points off", "ignore": "no penalty", "penalty2_first": "2 points off the first record shot",
             "miss_first": "first record shot scored as a miss", "upheld": "upheld", "not_upheld": "not upheld", "withdrawn": "withdrawn",
             "justified": "justified", "not_justified": "not justified", "found": "found", "not_found": "not found (a miss)"}

    def incidents(self, mid: int) -> dict:
        """The Range Incident Report (ISSF 6.10.4 h, CMP 3PAR 6.5, CMP AG 7.11 RIF, NRA Range Officer's Notation): every irregularity,
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
        rows, later = [], rb.later_relay(rb.book_of(fmt))[1]
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
                         "rule": (later or "") if k in ("left_relay", "joins_relay") else d.get("rule") or "", "by": d.get("by", "")})
        decided = {r["shot_id"]: r for r in c.execute("SELECT * FROM shot_decisions WHERE match_id=?", (mid,))}
        if m["state"] != "ready":
            for f in self.flags(c, m):
                dd = decided.get(f["shot_id"])
                rows.append({"at_ms": f["event_ms"], "lane": f["lane"], "competitor": who_of.get(f["lane"], ""),
                             "what": "Flagged %s shot %s (%s)" % ("record" if f["stream"] == "scored" else "sighting", f["n"], "miss" if f["miss"] else "%.1f" % f["decimal"]),
                             "detail": f["why"] + (" · decided: " + self.WORDS.get(f["decision"], f["decision"]) if f["decision"] else " · NOT DECIDED"),
                             "rule": (f["guide"] or {}).get("rule") or "", "by": dd["decided_by"] if dd else ""})
            for u in self.unplaced(c, m):
                rows.append({"at_ms": u["event_ms"], "lane": u["lane"], "competitor": who_of.get(u["lane"], ""),
                             "what": "Shot heard but not placed", "detail": u["why"] + (" · decided: " + {"ignore": "not a shot (counts nothing)",
                             "miss": "a shot, scored as a miss"}[u["decision"]] if u["decision"] else " · NOT DECIDED"),
                             "rule": "", "by": u["decided_by"] or ""})
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
        return {"match_id": mid, "name": m["name"], "format_name": fmt["name"], "rulebook": rb.BOOKS[rb.book_of(fmt)]["name"], "started_ms": m["started_ms"],
                "ended_ms": m["ended_ms"], "official_ms": m["official_ms"], "rows": rows}

    def lane_clock(self, c, m, ln, now: int, run: Optional[Run] = None) -> dict:
        """A lane's own clock, for the console and the shooter's screen: own = "moving" (time stands still until the
        range officer presses Ready), "own" (finishing their phase after the match went on), "extra" (more time than the
        match in this phase) or "" (the match clock). moved_from: the targets they left, oldest first."""
        fmt = json.loads(m["format_json"])
        if ln["left_ms"] is not None or (ln["start_phase"] > m["phase"] and m["state"] in LIVE):   # a later relay
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
        return {"own": own, "stop": (run or self._run(c, m["id"])).stop_reason(ln["device_id"]) if own == "moving" else None,
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
        run = self._run(c, r["id"])
        clock = self._clock(r, now)
        clock.update({"match_id": r["id"], "name": r["name"], "lane": r["lane"], "competitor": r["competitor"],
                      "in_record": run.switched(r["phase"], device_id) if r["phase"] >= 0 else False,
                      "sighters_any_time": rb.sighters_any_time(rb.book_of(json.loads(r["format_json"])))})
        ln = self._lane(c, r["id"], device_id)
        lc = self.lane_clock(c, r, ln, now, run)
        if lc["own"] == "waiting":                      # a later relay: rejoining at a later phase
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
        clock.update(run.position_view(r, ln, json.loads(r["format_json"])))   # ISSF 3-position: the position, the switch
        return clock

    # ------------------------------------------------------------------------------------------- results
    def counted(self, c, m, ln, flags: Dict[int, dict]) -> List[dict]:
        """One lane's counted record shots in firing order, after the match director's decisions and the corrections
        (fetcore.matchengine.counted)."""
        return me.counted(json.loads(m["format_json"]), ln, self._series_shots(c, ln["series_id"]), self._series_unplaced(c, ln["series_id"]),
                          flags, self.core.corrections.effects(c, m, ln["lane"]), self.discharged_at(c, m["id"]))

    @staticmethod
    def _decision_penalties(m, ln, flags: Dict[int, dict]) -> List[dict]:
        return me.decision_penalties(json.loads(m["format_json"]), ln, flags)

    def counted_row(self, c, m, ln, flags: Dict[int, dict]) -> tuple:
        """(counted shots, extras) for a results row (fetcore.matchengine.counted_row)."""
        eff = self.core.corrections.effects(c, m, ln["lane"])
        annulled = {sid: c.execute("SELECT series_no, stream, tenths, is_miss FROM shots WHERE id=?", (sid,)).fetchone() for sid in eff["annulled"]}
        return me.counted_row(json.loads(m["format_json"]), ln, self._series_shots(c, ln["series_id"]), self._series_unplaced(c, ln["series_id"]),
                              flags, eff, self.discharged_at(c, m["id"]), {k: v for k, v in annulled.items() if v is not None})

    entry_view = staticmethod(me.entry_view)

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
            entries.append(dict(self.entry_view(shots, decimal), summary=ranking.summary(shots, mf.layout(fmt)), sort_name=(name.split() or [""])[-1], **x,
                                lane=ln["lane"], device_id=ln["device_id"], competitor=name,
                                target=(t["name"] if t and t["name"] else ln["device_id"]),
                                pending=sum(1 for f in flags.values() if f["device_id"] == ln["device_id"] and not f["decision"])))
        rows = ranking.rank(entries, rules, decimal)
        for r in rows:
            r.pop("summary", None)
        return {"id": mid, "name": m["name"], "format_name": fmt["name"], "scoring": fmt.get("scoring"), "tie_rules": rules, "book_name": rb.BOOKS[rb.book_of(fmt)]["name"],
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
                r = self.results(int(mid))
                m = self._row(c, int(mid))
                if m["final_kind"]:
                    r["final"] = self.final_board(m)
                out.append(r)
            except (MatchError, ValueError):
                continue
        return out

    def final_board(self, m) -> Optional[dict]:
        """A final on the TV board: its standings (the competition's results), where the final is (shot k of n), the
        next decision, and who is in a shoot-off now."""
        view = self._final_view(m)
        if view is None:
            return None
        fmt = json.loads(m["format_json"])
        main = [(i, p) for i, p in enumerate(fmt["phases"]) if p["kind"] == "record" and p.get("lanes") is None]
        cur = m["phase"]
        fired = sum(p["shots"] for i, p in main if m["state"] == "done" or i < cur)       # the shots of the phases before this one
        p = fmt["phases"][cur] if 0 <= cur < len(fmt["phases"]) else {}
        nxt = next(((i, q) for i, q in main if i >= cur and q.get("out")), None) if m["state"] in ACTIVE else None
        if m["state"] in ACTIVE and p.get("round") is not None:      # a shoot-off: the decision it settles
            nxt = (p["round"], fmt["phases"][p["round"]])
        upto = sum(q["shots"] for i, q in main if nxt and i <= nxt[0])
        return dict(view, shots_done=fired, shots_total=sum(q["shots"] for _, q in main),
                    now=p.get("label") if m["state"] in ACTIVE else None,
                    shoot_off=p.get("lanes") if m["state"] in ACTIVE and p.get("lanes") else None,
                    next_out={"after": upto, "places": nxt[1]["out"] if isinstance(nxt[1]["out"], list) else [nxt[1]["out"]]} if nxt else None)
