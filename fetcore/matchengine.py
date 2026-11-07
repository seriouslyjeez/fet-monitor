"""The match engine's parts that need no database (P0a, docs/PLAN-deeptens.md): states, decisions, thresholds, and the
pure functions that read a match log or a list of shots. FeT Monitor's fetserver/matches.py keeps the engine that
reads and writes its database and imports these; deeptens runs the same rules on remote matches. Moved unchanged from
fetserver/matches.py; the rest of the engine follows in later steps (the plan: docs/PLAN-fetcore-engine.md, local).
"""
from __future__ import annotations

import json
from typing import List, Optional

from . import rulebooks as rb

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
                        "MISFIT_FLAG_UM", "UNPLACED_DECISIONS", "MatchError", "rules_of", "own_clock", "discharged_at")


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
