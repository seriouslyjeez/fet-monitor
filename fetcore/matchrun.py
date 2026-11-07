"""The match engine's commands, with no database (P0a step 5, docs/PLAN-fetcore-engine.md): a Run holds one match's
state (its row, its lanes, its log) and the facts it reads (shots, decisions, corrections); a command changes that
state and records what happened. The caller then stores the result in one transaction: run.match_changes(),
run.lane_changes(), run.new_log and run.modes (the targets to switch, in order). FeT Monitor's fetserver/matches.py
does that against SQLite; deeptens does the same against its own storage. Moved from fetserver/matches.py with the
rules unchanged (the module docstring there tells how a match runs).

Rows behave like the database's: row(), lanes() and lane() return copies (what a SELECT would have returned at that
moment); only the _upd* helpers change the live state, the way an UPDATE would.

facts: an object with shots(series_id) and unplaced(series_id) (rows in (event_ms, id) order), decisions()
({shot_id: decision}) and corrections() (the match's shot_corrections rows that are not void, in id order).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from . import matchengine as me
from . import matchformats as mf
from . import rulebooks as rb
from .matchengine import LIVE, PACING, MatchError, own_clock


def _add(a, b):
    """SQL's a + b: NULL if either is NULL (the commands were written as UPDATE ... SET col = col + ?)."""
    return None if a is None or b is None else a + b


def not_official(m) -> None:
    """Official results do not change: decisions, corrections, restarts and moves wait until they are reopened."""
    if m["official_ms"] is not None:
        raise MatchError("the results are official: reopen them first")


class Run:
    def __init__(self, m, lanes, log, facts, close_ms: Optional[int] = me.CLOSE_MS):
        self.m = dict(m)
        self._m0 = dict(self.m)
        self._live = [dict(ln) for ln in lanes]          # firing-point order
        self._l0 = [dict(ln) for ln in self._live]       # as loaded: the key of each lane is its device_id then
        self.log = [dict(r) for r in log]
        self.new_log: List[dict] = []
        self.modes: List[tuple] = []                     # (device_id, mode, session, at_ms), in the order switched
        self.facts = facts
        self.close_ms = close_ms
        self._next_id = max((r["id"] for r in self.log), default=0) + 1

    # ------------------------------------------------------------------------------------------- state and results
    def row(self) -> dict:
        return dict(self.m)

    def lanes(self) -> List[dict]:
        return [dict(ln) for ln in self._live]

    def lane(self, device_id: str) -> dict:
        ln = next((x for x in self._live if x["device_id"] == device_id), None)
        if ln is None:
            raise MatchError("that target is not in this match")
        return dict(ln)

    def _upd(self, **cols) -> None:
        self.m.update(cols)

    def _upd_lane(self, which: str, **cols) -> None:
        """UPDATE match_lanes SET cols WHERE device_id=which (cols may change the device_id: a move)."""
        for ln in self._live:
            if ln["device_id"] == which:
                ln.update(cols)

    def _upd_lanes(self, where, **cols) -> None:
        """UPDATE match_lanes SET cols WHERE where(lane); a value may be a function of the lane (e.g. extra_ms+carry_ms)."""
        for ln in self._live:
            if where(ln):
                ln.update({k: v(ln) if callable(v) else v for k, v in cols.items()})

    def _log(self, at: int, kind: str, phase, device_id, data=None) -> None:
        r = {"id": self._next_id, "match_id": self.m["id"], "at_ms": at, "kind": kind, "phase": phase, "device_id": device_id,
             "data": json.dumps(data) if data is not None else None}
        self._next_id += 1
        self.log.append(r)
        self.new_log.append(r)

    def match_changes(self) -> dict:
        return {k: v for k, v in self.m.items() if self._m0.get(k) != v or k not in self._m0}

    def lane_changes(self) -> List[tuple]:
        """[(device_id as loaded, {column: new value})] for every lane that changed."""
        out = []
        for old, ln in zip(self._l0, self._live):
            ch = {k: v for k, v in ln.items() if old.get(k) != v}
            if ch:
                out.append((old["device_id"], ch))
        return out

    # ------------------------------------------------------------------------------------------- reading the facts
    def fmt(self) -> dict:
        return json.loads(self.m["format_json"])

    def lane_devs(self, device_id: str) -> List[str]:
        return me.lane_devs(self.log, device_id)

    def _scored_since(self, series_id, t) -> int:
        if series_id is None or t is None:
            return 0
        return sum(1 for s in self.facts.shots(series_id) if s["stream"] == "scored" and s["event_ms"] >= t)

    def flags(self, m) -> List[dict]:
        lanes = self.lanes()
        fmt = json.loads(m["format_json"])
        return me.flags(fmt, self.log, lanes, {ln["series_id"]: self.facts.shots(ln["series_id"]) for ln in lanes if ln["series_id"] is not None},
                        {ln["series_id"]: self.facts.unplaced(ln["series_id"]) for ln in lanes if ln["series_id"] is not None},
                        self.facts.decisions(), {ln["lane"]: me.correction_effects(fmt, self.facts.corrections(), ln["lane"]) for ln in lanes},
                        self.close_ms)

    def counted(self, m, ln, flags: Dict[int, dict]) -> List[dict]:
        fmt = json.loads(m["format_json"])
        sid = ln["series_id"]
        return me.counted(fmt, ln, self.facts.shots(sid) if sid is not None else [], self.facts.unplaced(sid) if sid is not None else [],
                          flags, me.correction_effects(fmt, self.facts.corrections(), ln["lane"]), me.discharged_at(self.log))

    def position_state(self, ln, phase) -> tuple:
        """A record phase in several positions (ISSF 7.7.3), for one lane: (record shots so far in it, the position it is
        in (0, 1, 2), sighting between positions now?). Going to sighting and back to MATCH moves on to the next."""
        start = max((r for r in self.log if r["kind"] == "phase_start" and r["phase"] == phase), key=lambda r: r["id"], default=None)
        if start is None:
            return 0, 0, False
        devs = self.lane_devs(ln["device_id"])
        pos, between = 0, False
        for r in self.log:
            if r["kind"] in ("to_sighters", "start_record") and r["phase"] == phase and r["device_id"] in devs and r["id"] > start["id"]:
                if r["kind"] == "to_sighters":
                    between = True
                elif between:
                    pos, between = pos + 1, False
        return self._scored_since(ln["series_id"], start["at_ms"]), pos, between

    def position_view(self, m, ln, fmt) -> dict:
        """For the screens, during a record phase in several positions: which position this lane is in, whether it is
        sighting between positions, and whether it may switch to sighting now (its position's MATCH shots are done)."""
        phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
        p = fmt["phases"][phase] if 0 <= phase < len(fmt["phases"]) else None
        blk = mf.blocks(p) if p else None
        if not blk:
            return {}
        n, pos, between = self.position_state(ln, phase)
        pos = min(pos, len(blk) - 1)
        last = pos == len(blk) - 1
        nxt = p["positions"][pos + 1]["position"] if not last else p.get("sight_after") or p["positions"][pos]["position"]
        return {"position": nxt if between else p["positions"][pos]["position"], "between": between,
                "can_back": not between and (not last or bool(p.get("sight_after"))) and n >= blk[pos] and m["state"] == "running",
                "can_record": between and not last,      # sighting for what comes after the phase (6.17.3 e): no Start record
                "pos_shots": max(0, min(n, blk[pos]) - (blk[pos - 1] if pos else 0)), "pos_of": p["positions"][pos]["shots"]}

    def switched(self, phase, device_id) -> bool:
        """Has this lane started record in this phase (since its last start, since it was last moved, and since it last
        went back to sighters)?"""
        devs = self.lane_devs(device_id)
        start = max((r["id"] for r in self.log if r["kind"] == "phase_start" and r["phase"] == phase), default=None)
        ready = max((r["id"] for r in self.log if r["kind"] in ("lane_ready", "sighters") and (r["device_id"] in devs or r["device_id"] is None)), default=None)
        back = max((r["id"] for r in self.log if r["kind"] == "to_sighters" and r["phase"] == phase and r["device_id"] in devs), default=None)
        after = max(start or 0, ready or 0, back or 0)
        return any(r["kind"] == "start_record" and r["phase"] == phase and r["device_id"] in devs and r["id"] > after for r in self.log)

    def stop_reason(self, device_id) -> Optional[str]:
        """Why a lane's time stands still: "interrupt" / "malfunction", or None for a move (ended by Ready)."""
        devs = self.lane_devs(device_id)
        r = max((r for r in self.log if r["kind"] in ("move", "interrupt") and r["device_id"] in devs and r["phase"] is not None),
                key=lambda r: r["id"], default=None)
        return me._data(r)["reason"] if r and r["kind"] == "interrupt" else None

    def lane_left_ms(self, m, ln, now: int) -> int:
        return me.lane_left_ms(json.loads(m["format_json"]), m, ln, now)

    def remaining_ms(self, m, now: int) -> int:
        return me.remaining_ms(json.loads(m["format_json"]), m, now)

    # ------------------------------------------------------------------------------------------- switching targets
    @staticmethod
    def _session(m, ln) -> str:
        return ln["competitor"] or m["name"]

    def _set_mode(self, m, ln, mode: str, at: int) -> None:
        self.modes.append((ln["device_id"], mode, self._session(m, ln), at))

    def set_modes(self, m, mode: str, now: int, only: Optional[str] = None, everyone: bool = False) -> None:
        """Switch the lanes' targets; a lane on its own clock (moved shooter) is left alone unless named or everyone.
        A lane that left for a later relay (CMP 3PAR 7.18, CMP AG 7.12) is never switched; one that rejoins at a later phase stays in
        sighting until then."""
        phase = self.m["phase"]
        phases = json.loads(self.m["format_json"])["phases"]
        only_lanes = phases[phase].get("lanes") if 0 <= phase < len(phases) else None     # a shoot-off: only the tied fire
        for ln in self.lanes():
            if only and ln["device_id"] != only:
                continue
            if ln["left_ms"] is not None:
                continue
            if not only and not everyone and own_clock(ln):
                continue
            out = (ln["start_phase"] > phase and not everyone) or (only_lanes is not None and ln["lane"] not in only_lanes and not only) \
                or ln["out_place"] is not None
            if mode == "record" and 0 <= phase < len(phases) and mf.blocks(phases[phase]) and self.position_state(ln, phase)[2]:
                out = True                              # sighting between positions (ISSF 7.7.3): stays so until Start record
            self._set_mode(m, ln, "sighter" if out else mode, now)

    # ------------------------------------------------------------------------------------------- the clock
    def clear_lanes(self) -> None:
        self._upd_lanes(lambda ln: True, moving_since_ms=None, moving_left_ms=None, extra_ms=0, own_phase=None, own_left_ms=None,
                        own_since_ms=None, sighting=0)

    def start(self, by: str, now: int) -> None:
        """The match starts (the caller has given every lane a fresh series)."""
        self._upd(state="running", started_ms=now)
        self._log(now, "start", None, None, {"by": by})
        self.enter(self.row(), 0, now)

    def enter(self, m, i: int, now: int) -> None:
        """Start phase i now, or (officer pacing) hold at its start until the range officer presses Next."""
        fmt = json.loads(m["format_json"])
        p = fmt["phases"][i]
        hold = m["paced"] == "officer" and not p.get("nohold")       # finals: START follows LOAD without a hold
        self._upd(phase=i, state="holding" if hold else "running", phase_started_ms=None if hold else now, phase_used_ms=0,
                  phase_extra_ms=0, ended_ms=None)
        self._upd_lanes(lambda ln: ln["own_phase"] is None and ln["moving_since_ms"] is None, extra_ms=0, sighting=0)
        self._upd_lanes(lambda ln: ln["start_phase"] == i and ln["from_match"] is not None,
                        extra_ms=rb.later_relay(rb.book_of(fmt))[0] - p["seconds"] * 1000, sighting=1 if p["kind"] in ("record", "stage") else 0)
        self._upd_lanes(lambda ln: ln["start_phase"] == i and ln["from_match"] is not None, extra_ms=lambda ln: _add(ln["extra_ms"], ln["carry_ms"]))
        self._log(now, "phase_start", i, None, {"hold": True} if hold else None)
        if hold:
            self.set_modes(m, "sighter", now)            # nothing counts as record before START
        else:
            self.set_modes(m, "record" if p["kind"] == "record" else "sighter", now)

    def finish_phase(self, m, now: int, how: str) -> None:
        self._log(now, "phase_end", m["phase"], None, {"how": how})
        if m["final_kind"] and mf.FINAL_KINDS[m["final_kind"]]["elim"]:    # a final with eliminations: decide a place
            self.decide_place(m, now)
            m = self.row()
        fmt = json.loads(m["format_json"])
        self._upd_lanes(lambda ln: ln["own_phase"] == m["phase"] and ln["own_left_ms"] == 0 and ln["own_since_ms"] is None
                        and ln["moving_since_ms"] is None, own_phase=None, own_left_ms=None, sighting=0, extra_ms=0)   # their time ended early
        for ln in self.lanes():                           # a moved shooter with time left finishes this phase on their own
            if ln["left_ms"] is not None:
                continue
            if ln["own_phase"] is None and (ln["moving_since_ms"] is not None or ln["extra_ms"] > 0):
                moving = ln["moving_since_ms"] is not None
                self._upd_lane(ln["device_id"], own_phase=m["phase"], own_left_ms=None if moving else ln["extra_ms"],
                               own_since_ms=None if moving else now, extra_ms=0)
                self._log(now, "lane_overtime", m["phase"], ln["device_id"], {"left_ms": None if moving else ln["extra_ms"]})
            elif ln["own_phase"] is None and ln["sighting"]:          # no time of their own left: they go on with everyone
                self._upd_lane(ln["device_id"], sighting=0)
        nxt = m["phase"] + 1
        if nxt < len(fmt["phases"]):
            self.enter(m, nxt, now)
        elif any(own_clock(ln) for ln in self.lanes()):
            self._upd(state="overtime", phase_started_ms=now, ended_ms=None)
            self._log(now, "overtime", m["phase"], None)
            self.set_modes(m, "sighter", now)       # everyone else: nothing after STOP counts as record
        else:
            self.done(m, now)

    def done(self, m, now: int) -> None:
        self._upd(state="done", ended_ms=now, phase_started_ms=None)
        self._log(now, "end", None, None)
        self.set_modes(m, "sighter", now, everyone=True)   # nothing after STOP counts as record

    def tick(self, now: int) -> int:
        """End every phase of this match whose time is up. Returns how many phases ended."""
        ended = 0
        m = self.row()
        while m["state"] == "running" and self.remaining_ms(m, now) <= 0:
            # the phase ended at its exact end time, not at the tick: the timeline stays exact
            p_end = now - me.overrun(json.loads(m["format_json"]), m, now)
            self.finish_phase(m, p_end, "time")
            ended += 1
            m = self.row()
        if m["state"] == "running" and self.all_fired(m):         # finals: STOP when everyone has fired
            self.finish_phase(m, now + 1, "all fired")              # just after: the last shot is inside the shot's time
            ended += 1
        return ended + self.lane_tick(now)

    def final_scores(self, m) -> Dict[int, dict]:
        """A final's running scores per firing point: {lane: {"total": tenths of the final's shots (no shoot-offs),
        "rounds": {round: [shoot-off tenths ...]}}}."""
        fmt = json.loads(m["format_json"])
        wins = me.windows(self.log)
        flags = {f["shot_id"]: f for f in self.flags(m)}
        out = {}
        for ln in self.lanes():
            row = {"total": 0, "rounds": {}}
            for x in self.counted(m, ln, flags):
                w = next((w for w in wins if w["start"] <= x["t"] and (w["end"] is None or x["t"] < w["end"])), None)
                p = fmt["phases"][w["phase"]] if w else {}
                if p.get("lanes") is not None:
                    row["rounds"].setdefault(p.get("round"), []).append(x["tenths"])
                else:
                    row["total"] += x["tenths"]
            out[ln["lane"]] = row
        return out

    def decide_place(self, m, now: int) -> None:
        """After a deciding shot (a phase with "out": that place, or a list of places, lowest first) or a shoot-off
        for it: the lowest finalist takes that place and is eliminated (after the last shot, 1st and 2nd are decided);
        a tie inserts a one-shot shoot-off for the tied right after (the final's own rule: CMP AG 10.3.5, CMP 3PAR
        10.2.9 n, ISSF 6.17.2 h), repeated until broken. A place that does not exist (fewer finalists) is skipped."""
        fmt = json.loads(m["format_json"])
        p = fmt["phases"][m["phase"]]
        if p["kind"] != "record":
            return
        rnd = m["phase"] if p.get("out") else p.get("round")
        if rnd is None or (p.get("lanes") is not None and p.get("round") is None):
            return
        kind = mf.FINAL_KINDS[m["final_kind"]]
        outs = fmt["phases"][rnd]["out"]
        sc = self.final_scores(m)
        key = lambda ln: (sc[ln["lane"]]["total"], sc[ln["lane"]]["rounds"].get(rnd, []))
        for place in (outs if isinstance(outs, list) else [outs]):
            active = [ln for ln in self.lanes() if ln["out_place"] is None and ln["series_id"] is not None]
            if len(active) != place:
                continue
            low = min(key(ln) for ln in active)
            bottom = [ln for ln in active if key(ln) == low]
            if len(bottom) == 2 and fmt["phases"][rnd].get("countback") and isinstance(outs, list) and place - 1 in outs:
                # ISSF 6.17.3 i: two tied when two go out are both eliminated, ordered by countback (no shoot-off)
                flags = {f["shot_id"]: f for f in self.flags(m)}
                cb = {ln["lane"]: me.countback_3p([s["tenths"] for s in self.counted(m, ln, flags)]) for ln in bottom}
                if cb[bottom[0]["lane"]] != cb[bottom[1]["lane"]]:
                    lo, hi = sorted(bottom, key=lambda ln: cb[ln["lane"]])
                    for ln, pl in ((lo, place), (hi, place - 1)):
                        self._upd_lane(ln["device_id"], out_place=pl, out_ms=now)
                        self._log(now, "place", m["phase"], ln["device_id"], {"place": pl, "countback": "ISSF 6.17.3 i"})
                        self._set_mode(m, ln, "sighter", now)
                    return
            if len(bottom) > 1:                          # tied: a shoot-off for them, now
                lanes = sorted(ln["lane"] for ln in bottom)
                n = 1 + sum(1 for x in fmt["phases"] if x.get("lanes") is not None and x["kind"] == "record")
                so = mf.shoot_off_phases(lanes, n, kind["so_rule"], rnd, kind["so_s"], kind.get("attention", False))
                fmt["phases"][m["phase"] + 1:m["phase"] + 1] = so
                if len(fmt["phases"]) > mf.MAX_PHASES:   # never jam the clock: the match director decides by hand
                    self._log(now, "shoot_off_limit", m["phase"], None, {"lanes": lanes, "place": place})
                    return
                self._upd(format_json=json.dumps(fmt))
                self._log(now, "shoot_off", m["phase"] + len(so), None, {"lanes": lanes, "place": place, "auto": True})
                return
            out = bottom[0]
            decided = [(out, place)] + ([(next(ln for ln in active if ln is not out), 1)] if place == 2 else [])
            for ln, pl in decided:
                self._upd_lane(ln["device_id"], out_place=pl, out_ms=now)
                self._log(now, "place", m["phase"], ln["device_id"], {"place": pl})
                self._set_mode(m, ln, "sighter", now)

    def all_fired(self, m) -> bool:
        """A final's shot phase (early) ends as soon as every finalist in it has fired its shots (CMP 3PAR 10.4.6 c)."""
        fmt = json.loads(m["format_json"])
        p = fmt["phases"][m["phase"]]
        if not p.get("early") or p["kind"] != "record":
            return False
        start = max((r["at_ms"] for r in self.log if r["kind"] in ("phase_start", "go") and r["phase"] == m["phase"]), default=None)
        lanes = [ln for ln in self.lanes() if ln["series_id"] is not None and ln["left_ms"] is None and not own_clock(ln)
                 and ln["out_place"] is None
                 and (p.get("lanes") is None or ln["lane"] in p["lanes"])]
        return bool(lanes) and all(self._scored_since(ln["series_id"], start) >= p["shots"] for ln in lanes)

    def shoot_off(self, lanes: List[int], by: str, now: int) -> None:
        """A tie-breaking shot for the tied firing points of a final that is added to the qualification (CMP 3PAR 10.2.9 n,
        NRA 7.1.2 q), after the final's last shot: the match runs again for that one shot. Repeated until the tie is
        broken. (Finals with eliminations insert their shoot-offs themselves.)"""
        m = self.row()
        if not m["final_kind"]:
            raise MatchError("shoot-offs are for finals")
        if mf.FINAL_KINDS[m["final_kind"]]["elim"]:
            raise MatchError("this final shoots off its ties by itself, at once")
        if m["state"] != "done":
            raise MatchError("a shoot-off comes after the final's last shot")
        not_official(m)
        have = {ln["lane"] for ln in self.lanes()}
        if not isinstance(lanes, list) or len(set(lanes)) < 2 or not set(lanes) <= have:
            raise MatchError("choose at least two firing points of this final")
        fmt = json.loads(m["format_json"])
        kind = mf.FINAL_KINDS[m["final_kind"]]
        n = 1 + sum(1 for p in fmt["phases"] if p.get("lanes") is not None and p["kind"] == "record")
        so = mf.shoot_off_phases(sorted(set(lanes)), n, kind["so_rule"], None, kind["so_s"], kind.get("attention", False))
        fmt["phases"] += so
        if len(fmt["phases"]) > mf.MAX_PHASES:
            raise MatchError("too many shoot-offs")
        self._upd(format_json=json.dumps(fmt))
        self._log(now, "shoot_off", len(fmt["phases"]) - len(so), None, {"lanes": sorted(set(lanes)), "by": by})
        self.enter(self.row(), len(fmt["phases"]) - len(so), now)

    def lane_tick(self, now: int) -> int:
        """End the own phase of every moved shooter whose own time is up (at its exact end), then the match if it was
        only waiting for them."""
        n, last = 0, None
        m0 = self.row()
        for ln in self.lanes():
            if ln["own_phase"] is None and ln["moving_since_ms"] is None and ln["extra_ms"] < 0 and m0["state"] == "running":
                rem = self.remaining_ms(m0, now) + ln["extra_ms"]
                if rem <= 0:                            # less time than the match (CMP 3PAR 7.18.3, CMP AG 7.12.2): this shooter stops now
                    end = now + rem
                    self._upd_lane(ln["device_id"], own_phase=m0["phase"], own_left_ms=0, own_since_ms=None, extra_ms=0, sighting=0)
                    self._log(end, "lane_stop", m0["phase"], ln["device_id"])
                    self.set_modes(m0, "sighter", end, only=ln["device_id"])
                    n += 1
                continue
            if ln["own_phase"] is not None and ln["moving_since_ms"] is None and ln["own_since_ms"] is not None:
                end = ln["own_since_ms"] + ln["own_left_ms"]
                if end <= now:
                    self.lane_end(self.row(), ln, end)
                    n, last = n + 1, max(last or end, end)
        m = self.row()
        if m["state"] == "overtime" and not any(own_clock(ln) for ln in self.lanes()):
            self.done(m, last if last is not None else now)
        return n

    def lane_end(self, m, ln, at: int) -> None:
        """A moved shooter's own phase is over: they join whatever the match is doing now."""
        self._log(at, "lane_end", ln["own_phase"], ln["device_id"])
        self._upd_lane(ln["device_id"], own_phase=None, own_left_ms=None, own_since_ms=None, moving_since_ms=None, moving_left_ms=None,
                       extra_ms=0, sighting=0)
        p = json.loads(m["format_json"])["phases"][m["phase"]] if m["phase"] >= 0 else None
        mode = "record" if m["state"] in ("running", "paused") and p and p["kind"] == "record" else "sighter"
        self.set_modes(m, mode, at, only=ln["device_id"])

    def lanes_pause(self, now: int) -> None:
        for ln in self.lanes():
            if ln["own_since_ms"] is not None:
                self._upd_lane(ln["device_id"], own_left_ms=max(0, ln["own_left_ms"] - (now - ln["own_since_ms"])), own_since_ms=None)

    def lanes_resume(self, now: int) -> None:
        self._upd_lanes(lambda ln: ln["own_phase"] is not None and ln["moving_since_ms"] is None and ln["own_left_ms"] is not None
                        and ln["own_left_ms"] > 0, own_since_ms=now)

    # ------------------------------------------------------------------------------------------- commands
    def command(self, action: str, by: str, now: int, seconds: Optional[int] = None, paced: Optional[str] = None) -> None:
        """Range officer actions: go (START a held phase), pause, resume, next (end the phase now), extra (add seconds
        to this phase), abort, interrupt / interrupt_end (the whole range), pacing, discharge (after the end)."""
        m = self.row()
        if action == "discharge":                 # "You may discharge air downrange": after the relay's end only
            if m["state"] != "done":
                raise MatchError("only after the relay has ended (no one may still be firing)")
            if me.discharged_at(self.log) is not None:
                raise MatchError("already given")
            self._log(now, "discharge", None, None, {"by": by})
            return
        if m["state"] == "overtime":
            self.overtime_command(m, action, by, now)
            return
        if m["state"] not in LIVE:
            raise MatchError("the match is not running")
        fmt = json.loads(m["format_json"])
        p = fmt["phases"][m["phase"]]
        if action == "pacing":                    # switch how commands are given; takes effect at the next phase
            if paced not in PACING:
                raise MatchError("commands: spoken, officer or auto")
            self._upd(paced=paced)
            self._log(now, "pacing", m["phase"], None, {"paced": paced, "by": by})
        elif m["state"] == "holding" and action not in ("go", "next", "abort"):
            raise MatchError("the phase has not started: press Next to start it")
        elif action == "go":
            if m["state"] != "holding":
                raise MatchError("nothing is waiting to start")
            self._upd(state="running", phase_started_ms=now)
            self._log(now, "go", m["phase"], None, {"by": by})
            self.set_modes(m, "record" if p["kind"] == "record" else "sighter", now)
        elif action == "pause":
            if m["state"] != "running":
                raise MatchError("already paused")
            self._upd(state="paused", phase_used_ms=m["phase_used_ms"] + now - m["phase_started_ms"], phase_started_ms=None)
            self.lanes_pause(now)
            self._log(now, "pause", m["phase"], None, {"by": by})
        elif action == "interrupt":                    # the whole range stops (target failure, power, safety)
            if m["state"] != "running":
                raise MatchError("the clock is not running")
            self._upd(state="paused", phase_used_ms=m["phase_used_ms"] + now - m["phase_started_ms"], phase_started_ms=None)
            self.lanes_pause(now)
            self._log(now, "pause", m["phase"], None, {"by": by, "interruption": True})
        elif action == "interrupt_end":
            last = max((r for r in self.log if r["kind"] == "pause"), key=lambda r: r["id"], default=None)
            if m["state"] != "paused" or last is None or not me._data(last).get("interruption"):
                raise MatchError("the range is not interrupted")
            lost = (now - last["at_ms"]) // 1000
            extra, sighters, rule = rb.range_interruption(rb.book_of(fmt), lost)
            self._upd(state="running", phase_started_ms=now, phase_extra_ms=self.m["phase_extra_ms"] + extra * 1000)
            self.lanes_resume(now)
            self._log(now, "resume", m["phase"], None, {"by": by, "interruption": True, "lost_s": lost, "extra_s": extra, "sighters": sighters, "rule": rule})
            if sighters and p["kind"] in ("record", "stage"):        # unlimited sighters for everyone before record again
                them = [ln["device_id"] for ln in self.lanes() if not own_clock(ln) and ln["left_ms"] is None and ln["start_phase"] <= m["phase"]]
                for d in them:
                    self._upd_lane(d, sighting=1)
                self._log(now, "sighters", m["phase"], None, {"rule": rule})
                for d in them:
                    self.set_modes(m, "sighter", now, only=d)
        elif action == "resume":
            if m["state"] != "paused":
                raise MatchError("not paused")
            self._upd(state="running", phase_started_ms=now)
            self.lanes_resume(now)
            self._log(now, "resume", m["phase"], None, {"by": by})
        elif action == "next":
            if m["state"] in ("paused", "holding"):    # ending a paused or held phase: the pause ends with it
                if m["state"] == "paused":
                    self.lanes_resume(now)
                self._log(now, "resume", m["phase"], None, {"by": by})
                self._upd(state="running", phase_started_ms=now)
            self.finish_phase(self.row(), now, "officer")
        elif action == "extra":
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 3600:
                raise MatchError("extra time: 1 to 3600 seconds")
            self._upd(phase_extra_ms=self.m["phase_extra_ms"] + seconds * 1000)
            self._log(now, "extra", m["phase"], None, {"seconds": seconds, "by": by})
        elif action == "abort":
            self._log(now, "phase_end", m["phase"], None, {"how": "abort"})
            self.abort(m, now, by)
        else:
            raise MatchError("unknown action")

    def abort(self, m, now: int, by: str) -> None:
        self._upd(state="aborted", ended_ms=now, phase_started_ms=None)
        self._log(now, "abort", m["phase"], None, {"by": by})
        self.clear_lanes()
        self.set_modes(m, "sighter", now, everyone=True)

    def overtime_command(self, m, action: str, by: str, now: int) -> None:
        """The match time is over and only moved shooters are finishing: pause/resume their clocks, end now, or stop."""
        if action == "pause":
            if m["phase_started_ms"] is None:
                raise MatchError("already paused")
            self.lanes_pause(now)
            self._upd(phase_started_ms=None)
            self._log(now, "pause", m["phase"], None, {"by": by})
        elif action == "resume":
            if m["phase_started_ms"] is not None:
                raise MatchError("not paused")
            self.lanes_resume(now)
            self._upd(phase_started_ms=now)
            self._log(now, "resume", m["phase"], None, {"by": by})
        elif action == "next":
            if m["phase_started_ms"] is None:
                self._log(now, "resume", m["phase"], None, {"by": by})
            for ln in self.lanes():
                if own_clock(ln):
                    self.lane_end(m, ln, now)
            self.done(self.row(), now)
        elif action == "abort":
            self._log(now, "phase_end", m["phase"], None, {"how": "abort"})
            self.abort(m, now, by)
        else:
            raise MatchError("the match time is over: only moved shooters are still finishing")

    def restart(self, phase: int, by: str, now: int) -> None:
        """Go back to the start of `phase` (0 = the whole match), also after the match ended. The current phase ends
        now; shots fired since that phase first started stay in the record and are flagged for a decision."""
        m = self.row()
        if m["state"] == "ready":
            raise MatchError("the match has not started")
        not_official(m)
        fmt = json.loads(m["format_json"])
        if not isinstance(phase, int) or isinstance(phase, bool) or not 0 <= phase < len(fmt["phases"]):
            raise MatchError("no such phase")
        if m["state"] in LIVE:
            if m["state"] == "paused":
                self._log(now, "resume", m["phase"], None, {"by": by})
            self._log(now, "phase_end", m["phase"], None, {"how": "restart"})
        self._log(now, "restart", phase, None, {"by": by, "from": m["phase"], "state": m["state"]})
        self.clear_lanes()                  # moved shooters start the phase again with everyone
        self.enter(self.row(), phase, now)

    # ------------------------------------------------------------------------------------------- moving a shooter
    def move_check(self, from_dev: str) -> dict:
        """The match-side checks before moving a lane to another target (the caller then checks the target)."""
        m = self.row()
        if m["state"] in ("done", "aborted"):
            raise MatchError("the match is over")
        not_official(m)
        return self.lane(from_dev)

    def move(self, from_dev: str, to_dev: str, by: str, now: int) -> None:
        """Move a lane to another target. Before the start: just the target. During the match their own time stands
        still until lane_command 'ready' (the caller moves the series with them)."""
        m = self.row()
        ln = self.lane(from_dev)
        if m["state"] == "overtime" and not own_clock(ln):
            raise MatchError("this shooter has finished")
        phase = None
        if m["state"] != "ready":
            phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
            if ln["moving_since_ms"] is None:            # their time stands still from now
                self._upd_lane(from_dev, moving_since_ms=now, moving_left_ms=self.lane_left_ms(m, ln, now), own_since_ms=None, own_left_ms=None)
        self._upd_lane(from_dev, device_id=to_dev)
        self._log(now, "move", phase, to_dev, {"from": from_dev, "by": by})

    def resume_lane(self, m, ln, p, left: int, sighters: bool, now: int) -> None:
        """A shooter whose time stood still goes on with `left` ms in their phase (sighters first if allowed)."""
        sighting = 1 if sighters and p["kind"] in ("record", "stage") else 0
        if ln["own_phase"] is not None:
            counting = m["state"] not in ("paused",) and not (m["state"] == "overtime" and m["phase_started_ms"] is None)
            self._upd_lane(ln["device_id"], moving_since_ms=None, moving_left_ms=None, own_left_ms=left, own_since_ms=now if counting else None,
                           sighting=sighting)
        else:
            self._upd_lane(ln["device_id"], moving_since_ms=None, moving_left_ms=None, extra_ms=left - self.remaining_ms(m, now), sighting=sighting)
        mode = "sighter" if sighting or p["kind"] != "record" or m["state"] == "holding" else "record"
        self.set_modes(m, mode, now, only=ln["device_id"])

    def lane_command(self, device_id: str, action: str, by: str, now: int, seconds: Optional[int] = None) -> None:
        """One lane: 'ready' (a moved shooter is in position: their time runs again, plus `seconds` extra, default by
        the rulebook), 'extra' (seconds of extra time for this shooter only), 'start_record' (after sighters),
        'back_to_sighters', 'interrupt' / 'malfunction' / 'interrupt_end'."""
        m = self.row()
        if m["state"] not in LIVE and m["state"] != "overtime":
            raise MatchError("the match is not running")
        ln = self.lane(device_id)
        fmt = json.loads(m["format_json"])
        phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
        p = fmt["phases"][phase]
        if action == "ready":
            if ln["moving_since_ms"] is None:
                raise MatchError("this shooter is not moving")
            if seconds is None:
                seconds = rb.move_extra(rb.book_of(fmt))[0]
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 <= seconds <= 3600:
                raise MatchError("extra time: 0 to 3600 seconds")
            left = ln["moving_left_ms"] + seconds * 1000
            self.resume_lane(m, ln, p, left, True, now)
            self._log(now, "lane_ready", phase, device_id, {"extra_s": seconds, "left_ms": left, "by": by})
        elif action in ("interrupt", "malfunction") and (action == "interrupt" or rb.book_of(fmt) in (rb.NRA, rb.SB)):
            # the shooter's time stops: an interruption (ISSF 6.11.3, CMP AG 7.12, CMP 3PAR 7.18, NRA 9.2 b, NRA SB 10.3.2), or an
            # NRA disabled rifle (NRA 9.5, NRA SB 9.5 a)
            if ln["moving_since_ms"] is not None:
                raise MatchError("this shooter's time is already stopped")
            if m["state"] not in LIVE or m["state"] == "holding":
                raise MatchError("the clock is not running")
            self._upd_lane(device_id, moving_since_ms=now, moving_left_ms=self.lane_left_ms(m, ln, now), own_since_ms=None, own_left_ms=None)
            self._log(now, "interrupt", phase, device_id, {"reason": action, "by": by})
        elif action == "interrupt_end":
            if ln["moving_since_ms"] is None or self.stop_reason(device_id) is None:
                raise MatchError("this shooter is not interrupted")
            reason = self.stop_reason(device_id)
            lost = (now - ln["moving_since_ms"]) // 1000
            left, sighters, rule = rb.interruption_time(rb.book_of(fmt), reason, ln["moving_left_ms"], lost)
            self.resume_lane(m, ln, p, left, sighters, now)
            self._log(now, "interrupt_end", phase, device_id, {"reason": reason, "lost_s": lost, "left_ms": left, "sighters": sighters, "rule": rule, "by": by})
            if sighters:
                self._log(now, "sighters", phase, device_id, {"rule": rule})
        elif action == "malfunction":                # the clock keeps running (rulebooks.malfunction)
            allowable = seconds == 1                 # the range officer allows sighters (ISSF 6.13.2.1; no rulebook)
            sighters, extra, rule = rb.malfunction(rb.book_of(fmt), self.lane_left_ms(m, ln, now), allowable)
            if extra:
                col = "own_left_ms" if ln["own_phase"] is not None else "extra_ms"
                cur = self.lane(device_id)
                self._upd_lane(device_id, **{col: _add(cur[col], extra * 1000)})
            if sighters and p["kind"] in ("record", "stage"):
                self._upd_lane(device_id, sighting=1)
                self.set_modes(m, "sighter", now, only=device_id)
            self._log(now, "malfunction", phase, device_id, {"allowable": allowable, "sighters": sighters, "extra_s": extra, "rule": rule, "by": by})
            if sighters:
                self._log(now, "sighters", phase, device_id, {"rule": rule})
        elif action == "extra":
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 3600:
                raise MatchError("extra time: 1 to 3600 seconds")
            col = "moving_left_ms" if ln["moving_since_ms"] is not None else "own_left_ms" if ln["own_phase"] is not None else "extra_ms"
            cur = self.lane(device_id)
            self._upd_lane(device_id, **{col: _add(cur[col], seconds * 1000)})
            self._log(now, "lane_extra", phase, device_id, {"seconds": seconds, "by": by})
        elif action == "start_record":
            if ln["moving_since_ms"] is not None:
                raise MatchError("the shooter is moving to another target: press Ready first")
            counting = ln["own_phase"] is not None or m["state"] == "running"
            if p["kind"] == "record" and mf.blocks(p) and self.position_state(ln, phase)[2]:
                if not counting:
                    raise MatchError("record can start only while this shooter's time is running")
                if self.position_state(ln, phase)[1] + 1 >= len(mf.blocks(p)):   # sighting for what comes after (6.17.3 e)
                    raise MatchError(f"no more MATCH shots in {p['label'].lower()}: the {p.get('sight_after') or 'next'} shots start on command")
            elif ln["sighting"]:
                if p["kind"] not in ("record", "stage") or not counting:
                    raise MatchError("record can start only while this shooter's time is running")
            else:
                if p["kind"] != "stage" or not counting:
                    raise MatchError("the shooter starts record only during a running sighting-and-record stage")
                if self.switched(phase, device_id):
                    raise MatchError("already in record for this stage")
            self._upd_lane(device_id, sighting=0)
            self._log(now, "start_record", phase, device_id, {"by": by})
            self.set_modes(m, "record", now, only=device_id)
        elif action == "back_to_sighters" and p["kind"] == "record" and mf.blocks(p):
            # ISSF 7.7.3: after a position's MATCH shots the athlete switches to sighting for the next position
            blk = mf.blocks(p)
            n, pos, between = self.position_state(ln, phase)
            if m["state"] != "running" and ln["own_phase"] is None:
                raise MatchError("only while this shooter's time is running")
            if between:
                raise MatchError("already sighting for the next position")
            if pos >= len(blk) - 1 and not p.get("sight_after"):
                raise MatchError(f"{p['positions'][-1]['position']} is the last position: no sighters after its MATCH shots started")
            if n < blk[min(pos, len(blk) - 1)]:
                done = n - (blk[pos - 1] if pos else 0)
                raise MatchError(f"finish the {p['positions'][pos]['position']} shots first ({done} of {p['positions'][pos]['shots']}): "
                                 "sighters only between positions")
            self._log(now, "to_sighters", phase, device_id, {"by": by, "rule": rb.cite(rb.book_of(fmt), "7.7.3")})
            self.set_modes(m, "sighter", now, only=device_id)
        elif action == "back_to_sighters":                 # NRA SB 9.2: sighting shots at any time; record goes on later
            book = rb.book_of(fmt)
            if not rb.sighters_any_time(book):
                raise MatchError(f"the {rb.BOOKS[book]['name']} rules allow no sighters once record has started")
            if ln["moving_since_ms"] is not None:
                raise MatchError("the shooter is moving to another target: press Ready first")
            counting = ln["own_phase"] is not None or m["state"] == "running"
            if p["kind"] != "stage" or not counting:
                raise MatchError("back to sighters only during a running sighting-and-record stage")
            if not self.switched(phase, device_id):
                raise MatchError("not in record: already sighting")
            self._log(now, "to_sighters", phase, device_id, {"by": by, "rule": rb.cite(book, "9.2")})
            self.set_modes(m, "sighter", now, only=device_id)
        else:
            raise MatchError("unknown action")
