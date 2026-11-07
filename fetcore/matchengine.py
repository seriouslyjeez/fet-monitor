"""The match engine's parts that need no database (P0a, docs/PLAN-deeptens.md): states, decisions, thresholds, and the
pure functions that work out a match's timeline, clock, flags and counted shots from plain facts. FeT Monitor's
fetserver/matches.py loads those facts from its database and calls these; deeptens runs the same rules on remote
matches. Moved from fetserver/matches.py and fetserver/corrections.py with the rules unchanged; the commands (what a
command writes) follow in a later step (the plan: docs/PLAN-fetcore-engine.md, local).

The facts, as dicts (sqlite3.Row works too) with the column names of FeT Monitor's tables:
  m          the match: phase, state, paced, phase_started_ms, phase_used_ms, phase_extra_ms (fmt = its format, parsed)
  log        match_log rows in log order: id, at_ms, kind, phase, device_id, data (JSON text or None)
  lanes      match_lanes rows in firing-point order
  shots      {series_id: shot rows in firing order (event_ms, id)}: id, event_ms, stream, series_no, is_miss, tenths,
             ring, inner, ax_h, ay_h, misfit_um
  unplaced   {series_id: unplaced rows in (event_ms, id) order}: id, event_ms, stream, decision, decided_by
  decisions  {shot_id: decision} (shot_decisions)
  effects    {lane: correction_effects(...)} (shot_corrections)
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from . import faces
from . import matchformats as mf
from . import rulebooks as rb
from .rules import guidance, lookup_text

NEVER = 2 ** 62              # "no discharge command": every shot time is before it

ACTIVE = ("ready", "running", "paused", "holding", "overtime")
LIVE = ("running", "paused", "holding")
ACTIVE_SQL = "('ready','running','paused','holding','overtime')"
PACING = ("spoken", "officer", "auto")   # spoken: the console speaks every command (default); officer: the range officer
# reads them and the clock waits for Next at every phase; auto: phases follow each other with no commands at all
DECISIONS = ("count", "annul", "miss", "penalty2", "annul_penalty2")   # for a flagged RECORD shot; the last: too many
# shots, annulled and 2 points off the lowest shot of the first series (ISSF 6.11.5, CMP AG 7.11.8, CMP 3PAR 7.17.8 a)
SIGHTER_DECISIONS = ("ignore", "penalty2_first", "miss_first")    # for a flagged SIGHTING shot: it never becomes a record
# shot, but rules punish some of them on the first record shot (e.g. ISSF 6.11.1.1 h, k)
MAX_LANES = 100
CLOSE_MS = 3000              # a second report this soon after a shot: a double load, an echo or noise (no air rifle or
                             # air pistol is loaded and fired again in 3 s)
MISFIT_FLAG_UM = 2000        # the four microphone times disagree by more: a double load or a disturbed shot
UNPLACED_DECISIONS = ("ignore", "miss")   # a shot heard but not placed: noise (counts nothing), or a shot (a miss)


class MatchError(ValueError):
    pass


MATCH_ENGINE_EXPORTS = ("ACTIVE", "LIVE", "ACTIVE_SQL", "PACING", "DECISIONS", "SIGHTER_DECISIONS", "MAX_LANES", "CLOSE_MS",
                        "MISFIT_FLAG_UM", "UNPLACED_DECISIONS", "MatchError", "rules_of", "own_clock", "discharged_at",
                        "windows", "remaining_ms", "overrun", "clock", "lane_left_ms", "lane_devs", "countback_3p",
                        "correction_effects", "flags", "unplaced_reports", "decision_penalties", "counted", "counted_row",
                        "entry_view")


def discharged_at(log) -> Optional[int]:
    """When "You may discharge air downrange" was given, from a match log (rows with kind and at_ms, in log order): the
    latest such command with no restart after it (a restart reopens the match). None if not given."""
    at = None
    for r in log:
        if r["kind"] == "discharge":
            at = r["at_ms"]
        elif r["kind"] == "restart":
            at = None
    return at


def rules_of(fmt: dict) -> str:
    """A format's tie-break rules: its rulebook's (rulebooks.tie_of)."""
    return rb.tie_of(fmt)


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


def _check(s, heard, close_ms: Optional[int]) -> Optional[tuple]:
    """(why, code, pair) for a shot fired in its time that may not be one clean shot: another report on the same firing
    point within CLOSE_MS before it (a double load, an echo, the paper drive, noise), or four microphone times that
    disagree (a double load, a disturbed shot). None if neither."""
    t = s["event_ms"]
    prev = [] if close_ms is None else [h for h in heard if (h[0], h[1] if not h[3] else 0) < (t, s["id"]) and t - h[0] <= close_ms
                                        and not (h[1] == s["id"] and not h[3])]
    if prev:
        h = max(prev, key=lambda h: h[0])
        what = "a report heard but not placed" if h[3] else f"shot {h[2]}"
        return (f"{(t - h[0]) / 1000:.1f} s after {what}: a double load, an echo or noise?", "close",
                None if h[3] else {"shot_id": h[1], "n": h[2]})
    if s.get("misfit_um") is not None and s["misfit_um"] > MISFIT_FLAG_UM:
        return (f"the four microphones disagree by {s['misfit_um'] / 1000:.1f} mm: a double load or a disturbed shot?", "inconsistent", None)
    return None


def _data(r) -> dict:
    """A log or correction row's data: JSON text (FeT Monitor's column), a dict, or nothing."""
    d = r["data"]
    return json.loads(d) if isinstance(d, str) else dict(d) if d else {}


# ------------------------------------------------------------------------------------------------- timeline and clock
def windows(log) -> List[dict]:
    """The timeline as intervals: {phase, attempt, start, end, live, hold, superseded}. live False = the clock was
    paused (hold True: waiting for START). superseded: a later restart went back to this phase or an earlier one."""
    out, cur, attempts, restarts = [], None, {}, []
    for r in log:
        k = r["kind"]
        data = _data(r)
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


def remaining_ms(fmt: dict, m, now: int) -> int:
    """The match clock: time left in the current phase (0 when the match is not live)."""
    if m["phase"] < 0 or m["state"] not in LIVE:
        return 0
    p = fmt["phases"][m["phase"]]
    used = m["phase_used_ms"] + (now - m["phase_started_ms"] if m["state"] == "running" and m["phase_started_ms"] else 0)
    return max(0, p["seconds"] * 1000 + m["phase_extra_ms"] - used)


def overrun(fmt: dict, m, now: int) -> int:
    """How long ago a running phase's time ran out (0 if it has not)."""
    p = fmt["phases"][m["phase"]]
    used = m["phase_used_ms"] + (now - m["phase_started_ms"])
    return max(0, used - (p["seconds"] * 1000 + m["phase_extra_ms"]))


def clock(fmt: dict, m, now: int) -> dict:
    """What the clock screens show: the phase, its commands and the time left."""
    i = m["phase"]
    p = fmt["phases"][i] if 0 <= i < len(fmt["phases"]) else None
    nxt = fmt["phases"][i + 1] if p and i + 1 < len(fmt["phases"]) else None
    rem = remaining_ms(fmt, m, now)
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


def lane_left_ms(fmt: dict, m, ln, now: int) -> int:
    """A lane's own time left in its phase."""
    if ln["moving_since_ms"] is not None:
        return ln["moving_left_ms"] or 0
    if ln["own_phase"] is not None:
        return max(0, ln["own_left_ms"] - (now - ln["own_since_ms"] if ln["own_since_ms"] is not None else 0))
    return remaining_ms(fmt, m, now) + ln["extra_ms"] if m["state"] in LIVE else 0


def lane_devs(log, device_id: str) -> List[str]:
    """Every target a lane has used, newest first (a shooter may have been moved)."""
    devs, cur = [device_id], device_id
    for r in reversed(log):
        if r["kind"] == "move" and r["device_id"] == cur:
            cur = _data(r)["from"]
            devs.append(cur)
    return devs


def countback_3p(tenths: List[int]) -> tuple:
    """ISSF 6.17.3 i, for two finalists tied after the second standing series (shots 1-10 kneeling, 11-20 prone,
    21-25 and 26-30 standing): the second standing series, the first, then the last prone shot and on backwards."""
    return (sum(tenths[25:30]), sum(tenths[20:25])) + tuple(reversed(tenths[:20]))


# ------------------------------------------------------------------------------------------------- corrections
def correction_effects(fmt: dict, corrections, lane: int) -> dict:
    """What the corrections do to one firing point: annulled shot ids, overrides by shot id, added shots (incl.
    crossfire misses), penalties, disqualification. corrections: the match's shot_corrections rows that are not void,
    in id order (kind, lane, shot_id, data, created_by, resolved_by)."""
    book = rb.book_of(fmt)
    say = lambda key: rb.rule(book, key) or "the match director decides"
    out = {"annulled": {}, "overrides": {}, "added": [], "penalties": [], "dq": None}
    for r in corrections:
        d = _data(r)
        k = r["kind"]
        if r["lane"] == lane:
            if k == "disturbance" and d.get("outcome") == "justified":
                out["annulled"][r["shot_id"]] = {"id": r["id"], "kind": "annul", "from_lane": None, "note": "disturbance (%s)" % say("disturbance")}
            elif k == "double_load":
                out["annulled"][d["annul_shot"]] = {"id": r["id"], "kind": "annul", "from_lane": None,
                                                    "note": "double load: the lower shot (%s)" % say("double_load")}
            elif k == "not_registered" and d.get("outcome") in ("found", "not_found"):
                out["added"].append({"corr": r["id"], "at_ms": d["at_ms"], "tenths": d["tenths"], "ring": d["ring"], "inner": d["inner"],
                                     "miss": d["miss"], "added": True, "by": r["resolved_by"],
                                     "note": "did not register, %s (%s)" % ("found" if d["outcome"] == "found" else "not found: a miss", say("not_registered"))})
                if d.get("annul_shot"):
                    out["annulled"][d["annul_shot"]] = {"id": r["id"], "kind": "annul", "from_lane": None,
                                                        "note": "the extra shot after one that did not register (%s)" % say("not_registered")}
            elif k in ("annul", "crossfire"):
                out["annulled"][r["shot_id"]] = {"id": r["id"], "kind": k, "from_lane": d.get("from_lane"), "note": d.get("note")}
            elif k == "override":
                out["overrides"][r["shot_id"]] = dict(d, id=r["id"])          # the latest wins
            elif k == "added":
                out["added"].append({"corr": r["id"], "at_ms": d["at_ms"], "tenths": d["tenths"], "ring": d["ring"], "inner": d["inner"],
                                     "miss": d["miss"], "added": True, "note": d.get("note"), "by": r["created_by"]})
            elif k == "penalty":
                out["penalties"].append({"corr": r["id"], "points": d["points"], "rule": d["rule"], "note": d.get("note"), "shot_id": r["shot_id"]})
            elif k == "dq":
                out["dq"] = {"corr": r["id"], "rule": d["rule"], "official": d["official"], "note": d.get("note"), "by": r["created_by"]}
        if k == "crossfire" and d.get("from_lane") == lane:            # this firing point fired it
            if d.get("firer_stream") == "scored":
                out["added"].append({"corr": r["id"], "at_ms": d["at_ms"], "tenths": 0, "ring": 0, "inner": False, "miss": True,
                                     "crossfire_to": r["lane"], "note": "crossfire onto firing point %d" % r["lane"]})
            elif d.get("hit_stream") == "scored" and rb.rule(book, "sighter_crossfire"):
                out["penalties"].append({"corr": r["id"], "points": rb.sighter_crossfire_points(book), "rule": rb.rule(book, "sighter_crossfire"),
                                         "note": "sighter crossfired onto the record target of firing point %d" % r["lane"], "shot_id": None})
    return out


# ------------------------------------------------------------------------------------------------- flags
def flags(fmt: dict, log, lanes, shots: Dict[int, list], unplaced: Dict[int, list], decisions: Dict[int, str],
          effects: Dict[int, dict], close_ms: Optional[int] = CLOSE_MS) -> List[dict]:
    """Every shot of the match's lanes that the timeline says did not happen in its time, or is a record shot
    beyond the stage's count. Recomputed from the facts every time."""
    log = list(log)
    wins = windows(log)
    first = wins[0]["start"] if wins else None
    attempt_start: Dict[tuple, int] = {}
    for w in wins:
        attempt_start.setdefault((w["phase"], w["attempt"]), w["start"])
    out = []
    discharged = discharged_at(log)
    pauses = _pauses(log)
    for ln in lanes:
        if ln["series_id"] is None:
            continue
        devs = set(lane_devs(log, ln["device_id"]))
        mine = [r for r in log if r["device_id"] in devs]
        switches = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] == "start_record"]
        backs = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] == "to_sighters"]       # NRA SB 9.2, ISSF 7.7.3
        pos_log = [(r["phase"], r["at_ms"], 1 if r["kind"] == "to_sighters" else 0) for r in mine if r["kind"] in ("to_sighters", "start_record")]
        readies = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] in ("lane_ready", "sighters")] + \
            [(r["phase"], r["at_ms"]) for r in log if r["kind"] == "sighters" and r["device_id"] is None]
        stops = [(r["phase"], r["at_ms"]) for r in mine if r["kind"] == "lane_stop"]
        moving, own = _lane_spans(log, devs, wins)

        def sighting_ok(ph, a_start, t):          # after a move, or rejoining from an earlier relay (CMP 3PAR 7.18.3, CMP AG 7.12.2):
            rejoin = [a_start] if ph == ln["start_phase"] and ln["from_match"] is not None else []   # unlimited sighters
            ready = max([at for q, at in readies if q == ph and a_start <= at <= t] + rejoin, default=None)   # until record starts
            return ready is not None and not any(q == ph and ready <= at < t for q, at in switches)

        def pos_events(ph, a_start, t, sighter):  # ISSF 7.7.3: to sighting (1) and back to MATCH (0), in log order. A
            # command in the same millisecond as the shot came before it when it explains the shot's stream: a
            # sighter after "to sighting", a record shot after "back to MATCH"
            before = [k for q, at, k in pos_log if q == ph and a_start <= at < t]
            same = [k for q, at, k in pos_log if q == ph and a_start <= at == t]
            want = 1 if sighter else 0
            return before + (same[:len(same) - same[::-1].index(want)] if want in same else [])

        def between_at(ph, a_start, t):           # sighting between positions when the shot was fired
            ev = pos_events(ph, a_start, t, True)
            return bool(ev) and ev[-1] == 1

        def pos_at(ph, a_start, t):               # the position a record shot belongs to: one more after each switch back
            pos, btw = 0, False
            for k in pos_events(ph, a_start, t, False):
                if k:
                    btw = True
                elif btw:
                    pos, btw = pos + 1, False
            return pos

        def in_record(ph, a_start, t):            # an NRA-style stage: record started (and no move, nor back to sighters, since)
            sw = max((at for q, at in switches if q == ph and at >= a_start and t > at), default=None)
            return sw is not None and not any(q == ph and sw <= at <= t for q, at in readies + backs)
        counted: Dict[tuple, int] = {}
        eff = effects[ln["lane"]]
        items = [dict(r) for r in shots.get(ln["series_id"], ()) if r["id"] not in eff["annulled"]]      # an annulled shot needs no flag
        items += [{"id": None, "event_ms": a["at_ms"], "stream": "scored"} for a in eff["added"]]     # added shots count, never flagged
        items.sort(key=lambda r: (r["event_ms"], r["id"] or 0))
        heard = [(r["event_ms"], r["id"], r["series_no"], False) for r in items if r["id"] is not None] + \
            [(u["event_ms"], u["id"], None, True) for u in unplaced.get(ln["series_id"], ())]
        for s in items:
            t = s["event_ms"]
            if discharged is not None and t >= discharged:
                continue                           # after "You may discharge air downrange": expected, never counted
            inside = lambda sp: sp["start"] <= t and (sp["end"] is None or t < sp["end"])
            o = next((sp for sp in own if inside(sp)), None)
            if o is not None:                      # finishing their own phase after the match went on
                w = {"phase": o["phase"], "attempt": o["attempt"], "start": o["start"], "end": o["end"], "hold": False,
                     "live": not any(inside(pz) for pz in pauses), "superseded": o["superseded"]}
            else:
                w = next((w for w in wins if w["start"] <= t and (w["end"] is None or t < w["end"])), None)
            why, code, kind, pair = None, None, None, None   # code + the phase's kind: what the rulebook guidance looks up
            stopped = next((sp for sp in moving if inside(sp)), None)
            if stopped is not None:
                why, code = stopped["why"], "moving"
            elif w is None:
                code = "before_start" if first is None or t < first else "after_end"
                why = "before the start" if code == "before_start" else "after the end"
            elif w["superseded"]:
                why, code = f"fired before {fmt['phases'][w['phase']]['label'].lower()} was restarted", "restarted"
            elif w["phase"] < ln["start_phase"]:
                why, code = f"before this shooter rejoins at {fmt['phases'][ln['start_phase']]['label'].lower()}", "waiting"
            elif any(ph == w["phase"] and at <= t and at >= attempt_start[(w["phase"], w["attempt"])] for ph, at in stops):
                why, code = "after this shooter's own time", "after_own_time"
            elif ln["out_place"] is not None and t >= ln["out_ms"] and ln["out_place"] > 1:
                why, code = "after being eliminated", "eliminated"
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
                check = _check(s, heard, close_ms) if s["id"] is not None else None
                if p["kind"] not in mf.FIRE_KINDS:
                    why, code = f"during {p['label'].lower()} (no firing)", "no_fire"
                elif p["kind"] == "sight" and rec:
                    why, code = f"a record shot during {p['label'].lower()}", "record_in_sight"
                elif p["kind"] == "record" and not rec and not sighting_ok(w["phase"], attempt_start[(w["phase"], w["attempt"])], t) \
                        and not (mf.blocks(p) and between_at(w["phase"], attempt_start[(w["phase"], w["attempt"])], t)):
                    why, code = f"a sighting shot during {p['label'].lower()}", "sighter_in_record"
                elif p["kind"] == "stage" and not rec and in_record(w["phase"], attempt_start[(w["phase"], w["attempt"])], t):
                    why, code = "a sighting shot after record started", "sighter_after_record"
                elif check and not (rec and decisions.get(s["id"]) in ("count", "miss", "penalty2")):
                    why, code, pair = check                # not counted (yet): it does not use up a record shot
                elif rec:
                    key = (w["phase"], w["attempt"])
                    base = counted.get(key, ln["carry_shots"] if w["phase"] == ln["start_phase"] else 0)   # a later relay: shots already fired
                    blk = mf.blocks(p)
                    pos = pos_at(w["phase"], attempt_start[key], t) if blk else 0
                    if blk and pos < len(blk) and blk[pos] <= base < p["shots"]:
                        why, code = (f"an extra {p['positions'][pos]['position']} shot: after its {p['positions'][pos]['shots']} MATCH shots, "
                                     "without switching to sighting", "extra_in_position")
                    else:
                        counted[key] = base + 1
                        if counted[key] > p["shots"]:
                            why, code = f"more than {p['shots']} record shots in {p['label'].lower()}", "too_many"
                        elif check:                        # decided to count: still listed with its decision
                            why, code, pair = check
            if why and s["id"] is not None:
                out.append({"shot_id": s["id"], "device_id": ln["device_id"], "lane": ln["lane"], "n": s["series_no"],
                            "stream": s["stream"], "why": why, "event_ms": t, "decision": decisions.get(s["id"]),
                            "miss": bool(s["is_miss"]), "decimal": s["tenths"] / 10.0, "ring": s["ring"], "code": code, "pair": pair,
                            "guide": guidance(code, kind, s["stream"], rb.book_of(fmt))})
    return out


def unplaced_reports(lanes, shots: Dict[int, list], unplaced: Dict[int, list], discharged: Optional[int],
                     close_ms: Optional[int] = CLOSE_MS) -> List[dict]:
    """Record-time reports the target heard but could not place, on the match's firing points. They count as
    nothing until the director decides: ignore (noise, an echo) or miss (it was a shot)."""
    out = []
    until = discharged or NEVER
    for ln in lanes:
        if ln["series_id"] is None:
            continue
        mine = shots.get(ln["series_id"], ())
        for u in unplaced.get(ln["series_id"], ()):
            if u["stream"] != "scored" or u["event_ms"] >= until:
                continue
            before = [r for r in mine if close_ms is not None and r["event_ms"] <= u["event_ms"] <= r["event_ms"] + close_ms]
            prev = max(before, key=lambda r: r["event_ms"]) if before else None
            out.append({"id": u["id"], "lane": ln["lane"], "device_id": ln["device_id"], "event_ms": u["event_ms"],
                        "why": "heard but not placed by the target" + (
                            f", {(u['event_ms'] - prev['event_ms']) / 1000:.1f} s after shot {prev['series_no']}: a double load, an echo or noise?" if prev else ""),
                        "pair": {"shot_id": prev["id"], "n": prev["series_no"]} if prev else None,
                        "decision": u["decision"], "decided_by": u["decided_by"]})
    return out


# ------------------------------------------------------------------------------------------------- counted shots
def decision_penalties(fmt: dict, ln, flags: Dict[int, dict]) -> List[dict]:
    """2 points for each record shot the director annulled as one too many (decision annul_penalty2)."""
    rule = rb.rule(rb.book_of(fmt), "too_many") or "too many shots"
    return [{"points": 2, "rule": rule, "note": "shot %s: one too many" % f["n"], "shot_id": None}
            for f in flags.values() if f["device_id"] == ln["device_id"] and f["stream"] == "scored" and f["decision"] == "annul_penalty2"]


def counted(fmt: dict, ln, shots, unplaced, flags: Dict[int, dict], eff: dict, discharged: Optional[int]) -> List[dict]:
    """One lane's counted record shots in firing order, after the match director's decisions and the corrections.
    shots / unplaced: this lane's series' rows (see the module docstring). A record shot counts unless flagged; flagged
    ones only as decided (count as fired, miss = 0, penalty2 = 2 points off, not below 0; annul / undecided do not
    count). Corrections: annulled shots (also crossfires received) are left out; an override replaces the value (orig
    keeps the fired one); added shots and crossfire misses take their place in firing order (n None). Decisions on
    stray sighting shots land on the first record shot (miss_first: 0; penalty2_first: 2 points off each); penalties on
    their shot, or on the first series. Values: tenths (decimal x10) and ring, both after penalties."""
    out = []
    if ln["series_id"] is None:
        return out
    face = faces.face(fmt.get("face"))              # X's of higher value (NRA SB ties) from the position
    until = discharged or NEVER                     # nothing fired after "discharge air" counts
    for s in shots:
        if s["stream"] != "scored" or s["event_ms"] >= until or s["id"] in eff["annulled"]:
            continue
        f = flags.get(s["id"])
        decision = f["decision"] if f else "count"
        if decision not in ("count", "miss", "penalty2"):
            continue
        fired = {"tenths": s["tenths"], "ring": s["ring"], "miss": bool(s["is_miss"]), "inner": not s["is_miss"] and bool(s["inner"])}
        o = eff["overrides"].get(s["id"])
        v = {k: o[k] for k in ("tenths", "ring", "miss", "inner")} if o else fired
        miss = v["miss"] or decision == "miss"
        tenths, ring = (0, 0) if miss else (v["tenths"], v["ring"])
        if decision == "penalty2":
            tenths, ring = max(0, tenths - 20), max(0, ring - 2)
        inner = decision == "count" and not miss and v["inner"]
        out.append({"id": s["id"], "n": s["series_no"], "tenths": tenths, "ring": ring, "miss": miss,
                    "inner": inner, "higher": inner and not o and faces.higher(face, s["ax_h"], s["ay_h"]), "decision": decision if f else None,
                    "x_mm": None if s["is_miss"] else s["ax_h"] / 100.0, "y_mm": None if s["is_miss"] else s["ay_h"] / 100.0,
                    "t": s["event_ms"], "orig": fired if o else None, "override": o["note"] if o else None})
    for u in unplaced:
        if u["stream"] != "scored" or u["decision"] != "miss" or u["event_ms"] >= until:
            continue
        out.append({"id": None, "n": None, "tenths": 0, "ring": 0, "miss": True, "inner": False, "decision": "miss",
                    "x_mm": None, "y_mm": None, "t": u["event_ms"], "unplaced": True})      # heard, not placed: decided a shot
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
    for p in decision_penalties(fmt, ln, flags):   # too many shots: 2 points off the LOWEST shot of the first series
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


def counted_row(fmt: dict, ln, shots, unplaced, flags: Dict[int, dict], eff: dict, discharged: Optional[int],
                annulled_shots: Dict[int, dict]) -> tuple:
    """(counted shots, extras) for a results row. Disqualified: no shots (their values are cleared). extras: dq,
    penalties ({points, rule, note}) and removed (record shots annulled by a correction, for the score card).
    annulled_shots: {shot_id: shot row} for the ids in eff["annulled"] (series_no, stream, tenths, is_miss)."""
    out = counted(fmt, ln, shots, unplaced, flags, eff, discharged)
    removed = []
    for sid, a in eff["annulled"].items():
        r = annulled_shots.get(sid)
        if r is not None and r["stream"] == "scored":
            why = ("crossfire from firing point %d" % a["from_lane"] if a["from_lane"] else "crossfire (source unknown)") \
                if a["kind"] == "crossfire" else (a["note"] or "annulled")
            removed.append({"n": r["series_no"], "tenths": 0 if r["is_miss"] else r["tenths"], "why": why})
    book = rb.book_of(fmt)
    dq = dict(eff["dq"], text=lookup_text(eff["dq"]["rule"], book)) if eff["dq"] else None
    extras = {"dq": dq, "removed": sorted(removed, key=lambda x: x["n"] or 0),
              "penalties": [{"points": p["points"], "rule": p["rule"], "note": p["note"], "text": lookup_text(p["rule"], book)}
                            for p in decision_penalties(fmt, ln, flags) + eff["penalties"]]}
    return ([] if eff["dq"] else out), extras


def entry_view(shots: List[dict], decimal: bool) -> dict:
    """What a results row shows, from counted shots."""
    vals = [s["tenths"] if decimal else s["ring"] * 10 for s in shots]
    ser = [sum(vals[i:i + 10]) for i in range(0, len(vals), 10)]
    return {"total": sum(vals) / 10.0, "x": sum(1 for s in shots if s["inner"]), "shots": len(shots),
            "series": [v / 10.0 for v in ser], "last10": [v / 10.0 for v in vals[-10:]]}
