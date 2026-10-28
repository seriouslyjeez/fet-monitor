"""10 m Air Rifle match formats, as data. Every phase cites the rule it comes from (docs/MATCH-FORMATS.md has the
tables and editions). A custom format ("Design your own match") uses exactly the same structure.

A format is a list of phases fired in order. Phase kinds:
  setup      no live fire (equipment set-up, call to the line)
  sight      sighting shots only (target in SIGHTER)
  pause      no live fire; the targets are switched to record (ISSF / CMP "STOP ... START" gap)
  record     record shots only (target in RECORD); `shots` record shots allowed
  stage      NRA style: sighters until the shooter starts record, then record shots; one clock for both (8.1, 9.2 a)
  changeover no live fire (change position)
A shot fired in a phase that does not allow it, while the clock is paused, before the start or after the end, or
beyond the record shots of a stage is flagged for the match director (it is stored, never dropped).
Warnings are seconds before the end of the phase at which the command is given (and spoken on the RO console).
"""
from __future__ import annotations

import copy
import re
from typing import Optional, Dict, List

from . import rulebooks as rb

KINDS = {"setup", "sight", "pause", "record", "stage", "changeover"}
FIRE_KINDS = {"sight", "record", "stage"}
SCORING = {"decimal", "integer"}
POSITIONS = {"", "standing", "prone", "kneeling", "sitting"}
MAX_PHASES = 80                  # a final with its single shots and shoot-offs needs more than a qualification
FINALS = ("standard", "airgun", "sfz_standing", "sfz_3p", "issf", "nra")
MAX_SECONDS = 4 * 3600
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def P(kind, label, minutes=0, seconds=0, *, shots=0, position="", warnings=(), start="", end="", rule=""):
    return {"kind": kind, "label": label, "seconds": int(minutes * 60 + seconds), "shots": shots, "position": position,
            "warnings": [{"at": int(a), "say": s} for a, s in warnings], "start": start, "end": end, "rule": rule}


def _issf_qual():
    return {
        "id": "issf-ar60", "name": "10m Air Rifle 60 shots (ISSF / USA Shooting qualification)", "book": "ISSF", "rulebook": "ISSF", "tie_rules": "ISSF",
        "edition": "ISSF Rule Book 2026 (third print, eff. 19 Oct 2026)", "scoring": "decimal", "team_size": 3,
        "notes": "Range personnel switch the targets to match after the preparation and sighting time (6.10.4 b).",
        "phases": [
            P("setup", "Equipment set-up", 10, rule="6.11.1.1",
              start="Athletes to the line. Ten minutes to set up your equipment."),
            P("sight", "Preparation and sighting", 15, position="standing", warnings=[(30, "Thirty seconds")], rule="6.11.1.1 g-j",
              start="Fifteen minutes preparation and sighting time. Start.", end="End of preparation and sighting. Stop."),
            P("pause", "Targets to match", 0, 30, rule="6.11.1.1 j"),
            P("record", "Match firing", 75, shots=60, position="standing", rule="6.11.9.1, 6.11.1.2 e",
              warnings=[(600, "Ten minutes"), (300, "Five minutes")], start="Match firing. Start.", end="Stop. Unload."),
        ]}


def _cmp_3p(n):
    rec = {10: (10, 10, 15), 20: (20, 20, 25)}[n]
    rule = "5.3.2" if n == 10 else "5.3.3"
    w_s, w_r = [(30, "Thirty seconds")], [(300, "Five minutes remaining"), (120, "Two minutes remaining")]
    return {
        "id": f"cmp-3x{n}", "name": f"3x{n} Three-Position Air Rifle (CMP / NFHS National Standard)", "book": "CMP-3PAR", "rulebook": "CMP 3PAR", "tie_rules": "CMP",
        "edition": "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)", "scoring": "integer", "team_size": 4,
        "notes": "Kneeling, prone, standing. Scored in whole rings or tenths as the program announces (5.3.1 e). "
                 "For inexperienced 3x10 athletes the first preparation and sighting may be 15 or 20 minutes (5.3.1 d).",
        "phases": [
            P("setup", "Call to the line", 5, rule="7.6", start="You may move your rifles and equipment to the firing line."),
            P("sight", "Preparation and sighting, kneeling", 8, position="kneeling", warnings=w_s, rule=f"{rule}, 7.8",
              start="Preparation and sighting stage. Time limit eight minutes. Start.", end="Stop."),
            P("pause", "Targets to match fire", 0, 30, rule="7.8"),
            P("record", "Kneeling match fire", rec[0], shots=n, position="kneeling", warnings=w_r, rule=f"{rule}, 7.9",
              start=f"Kneeling match fire. {n} shots in a time limit of {rec[0]} minutes. Start.", end="Stop. Unload."),
            P("changeover", "Changeover, kneeling to prone", 5, rule="7.10", start="Your five-minute changeover stage begins now."),
            P("sight", "Sighting, prone", 5, position="prone", warnings=w_s, rule=f"{rule}, 7.11",
              start="Prone sighting stage. Time limit five minutes. Start.", end="Stop."),
            P("pause", "Targets to match fire", 0, 30, rule="7.11"),
            P("record", "Prone match fire", rec[1], shots=n, position="prone", warnings=w_r, rule=f"{rule}, 7.9",
              start=f"Prone match fire. {n} shots in a time limit of {rec[1]} minutes. Start.", end="Stop. Unload."),
            P("changeover", "Changeover, prone to standing", 5, rule="7.10", start="Your five-minute changeover stage begins now."),
            P("sight", "Sighting, standing", 5, position="standing", warnings=w_s, rule=f"{rule}, 7.11",
              start="Standing sighting stage. Time limit five minutes. Start.", end="Stop."),
            P("pause", "Targets to match fire", 0, 30, rule="7.11"),
            P("record", "Standing match fire", rec[2], shots=n, position="standing", warnings=w_r, rule=f"{rule}, 7.9",
              start=f"Standing match fire. {n} shots in a time limit of {rec[2]} minutes. Start.", end="Stop. Unload."),
        ]}


def _cmp_standing60():
    return {
        "id": "cmp-s60", "name": "60 shots standing (CMP National Standard)", "book": "CMP-3PAR", "rulebook": "CMP 3PAR", "tie_rules": "CMP",
        "edition": "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)", "scoring": "decimal", "team_size": 4,
        "notes": "Decimal scoring should be used with electronic targets (5.3.1 e).",
        "phases": [
            P("setup", "Call to the line", 5, rule="7.6", start="You may move your rifles and equipment to the firing line."),
            P("sight", "Preparation and sighting", 15, position="standing", warnings=[(30, "Thirty seconds")], rule="5.3.4, 7.8",
              start="Preparation and sighting stage. Time limit fifteen minutes. Start.", end="Stop."),
            P("pause", "Targets to match fire", 0, 30, rule="7.8"),
            P("record", "Standing match fire", 75, shots=60, position="standing", rule="5.3.4, 7.9",
              warnings=[(300, "Five minutes remaining"), (120, "Two minutes remaining")],
              start="Standing match fire. 60 shots in a time limit of 75 minutes. Start.", end="Stop. Unload."),
        ]}


def _cmp_standing3x20():
    """3PAR 5.3.4 'STANDING EVENT -- 3X20 SHOTS' with the commands of the CMP 60-shot standing script: take your
    positions (at least 5 minutes), 8 minutes preparation and sighting, record; after each record stage the targets are
    changed (with electronic targets: none to change), TAKE YOUR POSITIONS 30 seconds, a 5-minute standing sighting
    stage, record."""
    w_s, w_r = [(30, "Thirty seconds")], [(300, "Five minutes"), (120, "Two minutes")]
    phases = [P("setup", "Take your positions", 5, rule="script", start="You may uncase and handle your rifles. Take your positions."),
              P("sight", "Preparation and sighting", 8, position="standing", warnings=w_s, rule="5.3.4",
                start="Preparation and sighting time. Time limit 8 minutes. Start.", end="Sighting shots. Stop.")]
    for i in range(3):
        if i:
            phases.append(P("changeover", "Take your positions", 0, 30, rule="script",
                            start="Take your positions. Your standing position sighting stage will begin in 30 seconds."))
            phases.append(P("sight", "Standing sighting", 5, position="standing", warnings=w_s, rule="5.3.4",
                            start="Standing position sighting stage. Time limit five minutes. Start.", end="Sighting shots. Stop."))
        phases.append(P("pause", "Targets to match fire", 0, 30, rule="script"))     # electronic targets: reset to record
        phases.append(P("record", f"Standing record fire {i + 1} of 3", 25, shots=20, position="standing", warnings=w_r,
                        rule="5.3.4", start=("Standing record fire. " if i == 0 else "") + "20 shots in a time limit of 25 minutes. Start.",
                        end="Stop. Unload."))
    return {"id": "cmp-s3x20", "name": "60 shots standing in three 20-shot stages (CMP, paper-target style)", "book": "CMP-3PAR", "rulebook": "CMP 3PAR", "tie_rules": "CMP",
            "edition": "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)", "scoring": "decimal", "team_size": 4,
            "notes": "Rule 5.3.4 'STANDING EVENT -- 3X20 SHOTS': the 60-shot standing event fired in three 20-shot record "
                     "stages, as on paper targets. With electronic targets the 60-shot event may be fired without "
                     "changeover periods (5.3.4): '60 shots standing (CMP)' is the usual choice.", "phases": phases}


NRA_STANDING = {10: 15, 20: 25, 40: 50, 60: 75}


def _nra_stage(position, shots, first):
    sight = 8 if first else 5
    rec = shots if position in ("prone", "kneeling") else NRA_STANDING[shots]
    total = sight + rec
    return P("stage", f"{position.title()}: sighting and {shots} record shots", total, shots=shots, position=position,
             warnings=[(300, "Five minutes"), (120, "Two minutes")], rule="8.1, 8.2 a, 8.4, 9.2 a",
             start=f"{position.title()}. Sighting and {shots} record shots. Time limit {total} minutes. Start.",
             end="Cease fire. Unload.")


def _nra_3p(n):
    phases = []
    for i, pos in enumerate(("prone", "standing", "kneeling")):
        if i:
            phases.append(P("changeover", f"Position change to {pos}", 15, rule="8.2 b",
                            start=f"Position change. Fifteen minutes to change to {pos}."))
        phases.append(_nra_stage(pos, n, i == 0))
    return {"id": f"nra-3x{n}", "name": f"3x{n} Precision Air Rifle (NRA)", "book": "NRA", "rulebook": "NRA", "tie_rules": "NRA",
            "edition": "NRA Precision Air Rifle Rules 2025", "scoring": "integer", "team_size": 4,
            "notes": "Prone, standing, kneeling (7.1). One clock per position: sighting time plus record time (8.1); "
                     "sighters until the shooter starts record, then record only (9.2 a). Position change: 15 minutes "
                     "recommended, never less than 5 (8.2 b). Team size is set by the match program.",
            "phases": phases}


def _nra_standing(n):
    return {"id": f"nra-s{n}", "name": f"{n} shots standing (NRA Precision Air Rifle)", "book": "NRA", "rulebook": "NRA", "tie_rules": "NRA",
            "edition": "NRA Precision Air Rifle Rules 2025", "scoring": "integer", "team_size": 4,
            "notes": "One clock: 8 minutes sighting plus record time (8.1, 8.2 a); sighters until the shooter starts record.",
            "phases": [_nra_stage("standing", n, True)]}


AG_EDITION = rb.AG_EDITION


def _ag_ar60():
    """CMP Air Gun rules 6.4.1 b (electronic targets) and the CMP 60-shot script: call to the line, 15 minutes
    preparation and sighting (30 s warning, 7.9.1), straight on to 60 record shots in 75 minutes (10 and 5 minute
    warnings, 7.9.2)."""
    return {
        "id": "cmp-ag-ar60", "name": "10m Air Rifle 60 (AR60, CMP Air Gun Rules)", "book": "CMP-AG", "rulebook": "CMP Air Gun", "tie_rules": "CMP",
        "edition": AG_EDITION, "scoring": "decimal", "team_size": 4,
        "notes": "Electronic targets (6.4.1 b). Target carriers: 90 minutes record fire (6.4.1 d), set the time when creating the match. "
                 "Ties: last 10-shot series, then shot by shot from the last (8.5.1 b). Teams of 2, 3 or 4 as the program says (6.4.6).",
        "phases": [
            P("setup", "Call to the line", 15, rule="script", start="You may move your rifles and equipment to the firing line. "
              "Your preparation and sighting time will begin in fifteen minutes."),
            P("sight", "Preparation and sighting", 15, position="standing", warnings=[(30, "Thirty seconds")], rule="6.4.1 b, 7.9.1",
              start="Preparation and sighting time. Start. You will have fifteen minutes to fire unlimited sighting shots.",
              end="Preparation and sighting time. Stop."),
            P("record", "Record fire", 75, shots=60, position="standing", rule="6.4.1 b, 7.9.2",
              warnings=[(600, "Ten minutes"), (300, "Five minutes")],
              start="This event will be 60 record shots fired in a time limit of 75 minutes. Start.", end="Stop. Unload."),
        ]}


BUILTIN: Dict[str, dict] = {f["id"]: f for f in (
    _issf_qual(), _ag_ar60(), _cmp_3p(10), _cmp_3p(20), _cmp_standing60(), _cmp_standing3x20(),
    _nra_3p(10), _nra_3p(20), _nra_3p(40), _nra_standing(40), _nra_standing(60))}


def record_shots(fmt: dict) -> int:
    return sum(p["shots"] for p in fmt["phases"] if p["kind"] in ("record", "stage"))


def total_seconds(fmt: dict) -> int:
    return sum(p["seconds"] for p in fmt["phases"])


def validate(fmt) -> dict:
    """A clean copy of a format, or ValueError with a reason a person can act on. Used for custom formats and for
    per-match overrides of a standard one."""
    if not isinstance(fmt, dict):
        raise ValueError("a format must be an object")
    out = {"id": str(fmt.get("id") or ""), "name": str(fmt.get("name") or "").strip()[:80],
           "rulebook": str(fmt.get("rulebook") or "Custom")[:20], "edition": str(fmt.get("edition") or "")[:120],
           "scoring": fmt.get("scoring") or "decimal", "notes": str(fmt.get("notes") or "")[:1000],
           "team_size": fmt.get("team_size", 4), "tie_rules": fmt.get("tie_rules") or "ISSF", "phases": []}
    book = fmt.get("book") if "book" in fmt else rb.LEGACY.get(fmt.get("rulebook"), rb.NONE)     # formats saved before books
    if book not in rb.BOOKS:
        raise ValueError("rulebook: one of " + ", ".join(rb.BOOKS))
    out["book"] = book
    out["tie_rules"] = rb.BOOKS[book]["tie"] or out["tie_rules"]          # a book's formats break ties by that book
    if fmt.get("final") in FINALS:
        out["final"] = fmt["final"]
    if out["id"] and not ID_RE.match(out["id"]):
        raise ValueError("format id: lowercase letters, digits and dashes")
    if not out["name"]:
        raise ValueError("a format needs a name")
    if out["scoring"] not in SCORING:
        raise ValueError("scoring must be decimal or integer")
    if out["tie_rules"] not in ("ISSF", "CMP", "NRA"):
        raise ValueError("tie-break rules must be ISSF, CMP or NRA")
    if not isinstance(out["team_size"], int) or not 1 <= out["team_size"] <= 10:
        raise ValueError("team size must be 1 to 10")
    phases = fmt.get("phases")
    if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
        raise ValueError(f"a format needs 1 to {MAX_PHASES} phases")
    for i, p in enumerate(phases, 1):
        if not isinstance(p, dict):
            raise ValueError(f"phase {i} is not an object")
        kind = p.get("kind")
        if kind not in KINDS:
            raise ValueError(f"phase {i}: kind must be one of {', '.join(sorted(KINDS))}")
        secs = p.get("seconds")
        if not isinstance(secs, int) or isinstance(secs, bool) or not 0 < secs <= MAX_SECONDS:
            raise ValueError(f"phase {i}: time must be 1 second to 4 hours")
        shots = p.get("shots", 0)
        if kind in ("record", "stage"):
            if not isinstance(shots, int) or isinstance(shots, bool) or not 1 <= shots <= 200:
                raise ValueError(f"phase {i}: record shots must be 1 to 200")
        else:
            shots = 0
        pos = p.get("position", "") or ""
        if pos not in POSITIONS:
            raise ValueError(f"phase {i}: unknown position {pos!r}")
        warns = []
        for w in p.get("warnings") or []:
            if not isinstance(w, dict) or not isinstance(w.get("at"), int) or not 0 < w["at"] < secs:
                raise ValueError(f"phase {i}: a warning must be inside the phase")
            warns.append({"at": w["at"], "say": str(w.get("say") or "")[:80]})
        out["phases"].append({"kind": kind, "label": str(p.get("label") or kind).strip()[:80], "seconds": secs, "shots": shots,
                              "position": pos, "warnings": sorted(warns, key=lambda w: -w["at"]),
                              "start": str(p.get("start") or "")[:200], "end": str(p.get("end") or "")[:200],
                              "rule": str(p.get("rule") or "")[:60]})
        for flag in ("nohold", "early"):           # finals: no hold for START after LOAD; the shot ends when all have fired
            if p.get(flag) is True:
                out["phases"][-1][flag] = True
        for key in ("out", "round"):              # finals: the place(s) decided after this phase; a shoot-off's decision
            if isinstance(p.get(key), int) and not isinstance(p.get(key), bool):
                out["phases"][-1][key] = p[key]
        if isinstance(p.get("out"), list) and p["out"] and all(isinstance(x, int) and not isinstance(x, bool) for x in p["out"]):
            out["phases"][-1]["out"] = list(p["out"])      # several places, lowest first (3PAR 10.3.1: 8th and 7th)
        if isinstance(p.get("lanes"), list) and all(isinstance(x, int) and not isinstance(x, bool) for x in p["lanes"]):
            out["phases"][-1]["lanes"] = sorted(set(p["lanes"]))   # a shoot-off: only these firing points fire
    if not any(p["kind"] in ("record", "stage") for p in out["phases"]):
        raise ValueError("a format needs at least one record or stage phase")
    return out


def builtin_list() -> List[dict]:
    return [copy.deepcopy(f) for f in BUILTIN.values()]


COMMENTARY_S = 20                # spoken finals: time for the scores and commentary before the next LOAD (10.4.6 d)


def cmp_standard_final(spoken: bool = False) -> dict:
    """CMP Standard Final (10.4): 2 minutes to take positions, 5 minutes preparation and sighting (30 s warning), a
    30-second pause for the targets (10.2.9 b), then 10 shots standing, each on command: LOAD, 5 seconds, START,
    50 seconds or until everyone has fired, STOP (10.4.6)."""
    phases = [P("setup", "Take your positions", 0, 120, rule="10.4.4", start="Take your positions."),
              P("sight", "Preparation and sighting", 5, position="standing", warnings=[(30, "Thirty seconds")], rule="10.4.5",
                start="Preparation and sighting. Time limit five minutes. Start.", end="Stop. Unload."),
              P("pause", "Targets to match fire", 0, 30, rule="10.2.9 b")]
    for k in range(1, 11):
        if spoken and k > 1:                     # the range officer is not there to wait for: a pause for the scores
            phases.append(P("pause", "Scores after shot %d" % (k - 1), 0, COMMENTARY_S, rule="10.4.6 d"))
        phases.append(P("pause", "Shot %d: load" % k, 0, 5, rule="10.4.6",
                        start="For the %s competition shot. Load." % ("first" if k == 1 else "next")))
        phases.append(dict(P("record", "Shot %d" % k, 0, 50, shots=1, position="standing", rule="10.4.6", start="Start.", end="Stop."),
                           nohold=True, early=True))
    return validate({"id": "cmp-final-standard", "name": "CMP Standard Final", "book": "CMP-3PAR", "rulebook": "CMP 3PAR", "tie_rules": "CMP",
                     "edition": "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)", "scoring": "decimal", "final": "standard",
                     "notes": "Added to the qualification score (10.4.3); ties for 1st-3rd shot off (10.2.9 n).", "phases": phases})


def shoot_off_phases(lanes, n: int, rule: str = "10.2.9 n", round_: Optional[int] = None, seconds: int = 50,
                     attention: bool = False) -> list:
    """One tie-breaking shot for the tied firing points (3PAR 10.2.9 n, Air Gun 10.3.5, ISSF 6.17.2 h): LOAD, 5
    seconds, START, 50 seconds or until the tied have fired. NRA 7.1.2 q: the final's own procedure (LOAD, ATTENTION
    3 2 1, START, 75 seconds). round_: the phase whose decision it settles."""
    extra = {"round": round_} if round_ is not None else {}
    out = [dict(P("pause", "Shoot-off %d: load" % n, 0, 5, rule=rule,
                  start="These commands are for the tied athletes only, for your tie-breaking shot. Load."), lanes=list(lanes), **extra)]
    if attention:
        out.append(dict(P("pause", "Shoot-off %d: attention" % n, 0, 4, rule=rule, start="Attention. Three, two, one."),
                        nohold=True, lanes=list(lanes), **extra))
    out.append(dict(P("record", "Shoot-off %d" % n, 0, seconds, shots=1, position="standing", rule=rule, start="Start.", end="Stop."),
                    nohold=True, early=True, lanes=list(lanes), **extra))
    return out


ELIM_24 = {12: 8, 14: 7, 16: 6, 18: 5, 20: 4, 22: 3, 24: 2}      # 24-shot finals: after shot n, place p is decided (24: 1st and 2nd)
AIRGUN_OUT = ELIM_24


def cmp_airgun_final(spoken: bool = False) -> dict:
    """CMP Air Gun Final (Air Rifle and Air Pistol Rules 2026, 10.2; script 20 Feb 2026): take positions 30 s,
    preparation and sighting 5 min (30 s warning), a pause for the targets, 2 series of 5 shots in 250 s, then single
    shots in 50 s; after shots 12, 14 ... 22 the lowest finalist is eliminated, shot 24 decides 1st and 2nd (10.2.1).
    Every shot is LOAD, 5 s, START, and STOP at its time (10.2.5 c, 10.2.6 c). Spoken: a pause for the scores before
    each LOAD. Phases that decide a place carry "out": that place."""
    phases = [P("setup", "Take your positions", 0, 30, rule="10.2.2", start="Take your positions. Your preparation and sighting time will start in 30 seconds."),
              P("sight", "Preparation and sighting", 5, position="standing", warnings=[(30, "Thirty seconds")], rule="10.2.3",
                start="Preparation and sighting. Start. You will have five minutes to fire unlimited sighting shots.",
                end="Stop. Unload. Preparation and sighting time. Stop."),
              P("pause", "Targets to match fire", 0, 30, rule="10.2.4")]

    def load(label, say, k):
        if spoken and k > 1:
            phases.append(P("pause", "Scores and commentary", 0, COMMENTARY_S, rule="10.2.5 d"))
        phases.append(P("pause", label, 0, 5, rule="10.2.5", start=say))
    for k in (1, 2):
        load("Series %d: load" % k, "For the %s competition series. Load." % ("first" if k == 1 else "next"), k)
        phases.append(dict(P("record", "Series %d" % k, 0, 250, shots=5, position="standing", rule="10.2.5", start="Start.", end="Stop."), nohold=True))
    for n in range(11, 25):
        load("Shot %d: load" % n, "For the next competition shot. Load.", n)
        ph = dict(P("record", "Shot %d" % n, 0, 50, shots=1, position="standing", rule="10.2.6", start="Start.", end="Stop."), nohold=True)
        if n in AIRGUN_OUT:
            ph["out"] = AIRGUN_OUT[n]
        phases.append(ph)
    return validate({"id": "cmp-final-airgun", "name": "CMP Air Gun Final", "book": "CMP-AG", "rulebook": "CMP Air Gun", "tie_rules": "CMP", "edition": AG_EDITION,
                     "scoring": "decimal", "final": "airgun", "notes": "The final alone decides the places (10.1.1); every tie is shot off (10.3.5).",
                     "phases": phases})


class _Final:
    """Builds a final's phases: LOAD (5 s) before every shot or series, with a pause for the scores before it when
    the console speaks the commands (the range officer reading them takes the time the comments need)."""
    def __init__(self, spoken: bool, comment_rule: str):
        self.phases, self.spoken, self.comment_rule = [], spoken, comment_rule

    def add(self, *phases):
        self.phases += list(phases)

    def shot(self, label, say, seconds, shots, position, rule, out=None, warn=(), early=True, attention=False):
        if self.spoken and self.phases and self.phases[-1]["kind"] == "record":
            self.phases.append(P("pause", "Scores and commentary", 0, COMMENTARY_S, rule=self.comment_rule))
        self.phases.append(P("pause", label + ": load", 0, 5, rule=rule, start=say))
        if attention:
            self.phases.append(dict(P("pause", label + ": attention", 0, 4, rule=rule, start="Attention. Three, two, one."), nohold=True))
        ph = dict(P("record", label, 0, seconds, shots=shots, position=position, rule=rule, warnings=warn, start="Start.", end="Stop."), nohold=True)
        if early:
            ph["early"] = True
        if out is not None:
            ph["out"] = out
        self.phases.append(ph)


def _singles_24(f: _Final, rule: str) -> None:
    for n in range(11, 25):
        f.shot("Shot %d" % n, "For the next competition shot. Load.", 50, 1, "standing", rule, out=ELIM_24.get(n))


def issf_final(spoken: bool = False) -> dict:
    """ISSF 10 m Air Rifle Final (Rule Book 2026, 6.17.2): TAKE YOUR POSITIONS, 30 s; 5 minutes preparation and
    sighting (30 s warning); 60 s to clear the targets; 2 series of 5 shots in 250 s and 14 single shots in 50 s, each
    stopped at its time or when everyone has fired (6.17.2 e, f); the lowest finalist is eliminated after shots 12, 14
    ... 22, shot 24 decides gold and silver (g); a tie for an elimination is shot off at once (h)."""
    f = _Final(spoken, "6.17.2 e")
    f.add(P("setup", "Take your positions", 0, 30, rule="6.17.2 d", start="Take your positions."),
          P("sight", "Preparation and sighting", 5, position="standing", warnings=[(30, "Thirty seconds")], rule="6.17.2 d",
            start="Five minutes preparation and sighting time. Start.", end="Stop. Unload."),
          P("pause", "Targets cleared for match", 0, 60, rule="6.17.2 d"))
    for k in (1, 2):
        f.shot("Series %d" % k, "For the %s competition series. Load." % ("first" if k == 1 else "next"), 250, 5, "standing", "6.17.2 e")
    _singles_24(f, "6.17.2 f")
    return validate({"id": "issf-final-ar", "name": "ISSF 10m Air Rifle Final", "book": "ISSF", "rulebook": "ISSF", "tie_rules": "ISSF",
                     "edition": "ISSF Rule Book 2026 (Edition 2025, third print)", "scoring": "decimal", "final": "issf",
                     "notes": "Starts from zero (6.17.1.5); start positions by draw (6.17.1.2); ties at an elimination shot off (6.17.2 h).",
                     "phases": f.phases})


def cmp_sfz_standing_final(spoken: bool = False) -> dict:
    """CMP 3PAR Standing Start-from-Zero Final (10.5, ISSF style, after a 60-shot standing event) with the CMP script
    "ISSF Standing Final (24 shots start-from-zero)": take your positions 2 min; 5 min preparation and sighting (30 s
    warning); 30 s pause (10.2.9 b); 2 series of 5 in 250 s, then single shots in 50 s, each stopped at its time or when
    everyone has fired (10.5.3 c, 10.5.4 d); eliminations after shots 12, 14 ... 22, shot 24 decides 1st and 2nd
    (10.5.4 f); every tie shot off at once (10.2.9 n)."""
    f = _Final(spoken, "10.5.3 d")
    f.add(P("setup", "Take your positions", 2, rule="script", start="Take your positions."),
          P("sight", "Preparation and sighting", 5, position="standing", warnings=[(30, "Thirty seconds")], rule="10.5.2",
            start="Five minutes preparation and sighting. Start.", end="Sighting shots. Stop. Unload."),
          P("pause", "Targets to match fire", 0, 30, rule="10.2.9 b"))
    for k in (1, 2):
        f.shot("Series %d" % k, "For the %s competition series. Load." % ("first" if k == 1 else "next"), 250, 5, "standing", "10.5.3")
    _singles_24(f, "10.5.4")
    return validate({"id": "cmp-final-sfz-standing", "name": "CMP Standing Start-from-Zero Final", "book": "CMP-3PAR", "rulebook": "CMP 3PAR",
                     "tie_rules": "CMP", "edition": "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)", "scoring": "decimal",
                     "final": "sfz_standing", "notes": "The final alone decides (10.5); positions by draw; every tie shot off (10.2.9 n).",
                     "phases": f.phases})


def cmp_sfz_3p_final(spoken: bool = False) -> dict:
    """CMP 3PAR Three-Position Start-from-Zero Final (10.3, after 3x10 or 3x20) with the CMP script "ISSF Three-Position
    Final (35 shots start-from-zero)": take your positions 30 s; 5 min preparation and sighting kneeling; 10 shots
    kneeling in 400 s; 5 min changeover and sighting prone (the script; 10.3.1 prints "5 7-minutes"); 10 prone in
    300 s; 7 min changeover and sighting standing; 5 standing in 250 s; 5 standing in 250 s, then 8th and 7th are
    eliminated; single shots 31-35 in 50 s eliminate 6th, 5th, 4th, 3rd and decide 1st and 2nd (10.3.3 f). Series and
    shots stop at their time or when everyone has fired (10.3.2 c, 10.3.3 d); 30 s warnings (script)."""
    w = [(30, "Thirty seconds")]
    f = _Final(spoken, "10.3.2 d")
    f.add(P("setup", "Take your positions", 0, 30, rule="10.3.1", start="Take your positions."),
          P("sight", "Preparation and sighting, kneeling", 5, position="kneeling", warnings=w, rule="10.3.1",
            start="Five minutes preparation and sighting time. Start.", end="Sighting shots. Stop. Unload."),
          P("pause", "Targets to match fire", 0, 30, rule="10.2.9 b"))
    f.shot("Kneeling series", "For the first competition series, ten match shots kneeling in a time limit of 400 seconds. Load.",
           400, 10, "kneeling", "10.3.1, 10.3.2", warn=w)
    f.add(P("sight", "Changeover and sighting, prone", 5, position="prone", warnings=w, rule="10.3.1, script",
            start="Five minutes changeover and sighting time. Start.", end="Sighting shots. Stop. Unload."),
          P("pause", "Targets to match fire", 0, 30, rule="10.2.9 b"))
    f.shot("Prone series", "For the next competition series, ten match shots prone in a time limit of 300 seconds. Load.",
           300, 10, "prone", "10.3.1, 10.3.2", warn=w)
    f.add(P("sight", "Changeover and sighting, standing", 7, position="standing", warnings=w, rule="10.3.1",
            start="Seven minutes changeover and sighting time. Start.", end="Sighting shots. Stop. Unload."),
          P("pause", "Targets to match fire", 0, 30, rule="10.2.9 b"))
    for k in (1, 2):
        f.shot("Standing series %d" % k, "For the next competition series, five match shots standing in 250 seconds. Load.",
               250, 5, "standing", "10.3.1, 10.3.2", warn=w, out=[8, 7] if k == 2 else None)
    for n, place in zip(range(31, 36), (6, 5, 4, 3, 2)):
        f.shot("Shot %d" % n, "For the next competition shot. Load.", 50, 1, "standing", "10.3.3", out=place)
    return validate({"id": "cmp-final-sfz-3p", "name": "CMP Three-Position Start-from-Zero Final", "book": "CMP-3PAR", "rulebook": "CMP 3PAR",
                     "tie_rules": "CMP", "edition": "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)", "scoring": "decimal",
                     "final": "sfz_3p", "notes": "The final alone decides (10.3); positions by draw (10.3.1); every tie shot off (10.2.9 n).",
                     "phases": f.phases})


def nra_final(spoken: bool = False) -> dict:
    """NRA Precision Air Rifle final (7.1.2): 3 minutes preparation (d); 7 minutes sighting, 30 s warning (e, f); 30 s
    pause (g); 10 shots standing, each: LOAD, ATTENTION 3 2 1, START, 75 seconds, STOP after the last finalist has
    fired (m); every shot scored and announced before the next (n). Added to the qualification score (o); ties shot
    off shot for shot by the same procedure (q). Start positions by qualification rank (k)."""
    f = _Final(spoken, "7.1.2 n")
    f.add(P("setup", "Preparation period", 3, rule="7.1.2 d", start="Preparation period. Three minutes."),
          P("sight", "Sighting", 7, position="standing", warnings=[(30, "Thirty seconds")], rule="7.1.2 e, f",
            start="Sighting shots. Start.", end="Stop."),
          P("pause", "Targets to record", 0, 30, rule="7.1.2 g"))
    for k in range(1, 11):
        f.shot("Shot %d" % k, "For the %s competition shot. Load." % ("first" if k == 1 else "next"), 75, 1, "standing", "7.1.2 m",
               attention=True)
    return validate({"id": "nra-final", "name": "NRA Final", "book": "NRA", "rulebook": "NRA", "tie_rules": "NRA",
                     "edition": "NRA Precision Air Rifle Rules 2025", "scoring": "decimal", "final": "nra",
                     "notes": "Added to the qualification score (7.1.2 o); ties shot off, shot for shot (7.1.2 q).", "phases": f.phases})


# Every final: its book, how finalists are placed (a random draw, or the best qualifier first), whether eliminations
# decide the places (the final alone) or the final is added to the qualification, which ties are shot off, and the
# shoot-off procedure.
FINAL_KINDS = {
    "standard": {"book": "CMP-3PAR", "name": "CMP Standard Final (CMP 3PAR 10.4)", "build": cmp_standard_final, "draw": False, "elim": False,
                 "relay": True, "tie_places": 3, "so_rule": "10.2.9 n", "so_s": 50, "events": "any"},
    "sfz_standing": {"book": "CMP-3PAR", "name": "CMP Standing Start-from-Zero Final (CMP 3PAR 10.5)", "build": cmp_sfz_standing_final, "draw": True,
                     "elim": True, "relay": False, "so_rule": "10.2.9 n", "so_s": 50, "events": "standing"},
    "sfz_3p": {"book": "CMP-3PAR", "name": "CMP Three-Position Start-from-Zero Final (CMP 3PAR 10.3)", "build": cmp_sfz_3p_final, "draw": True,
               "elim": True, "relay": False, "so_rule": "10.2.9 n", "so_s": 50, "events": "3p"},
    "airgun": {"book": "CMP-AG", "name": "CMP Air Gun Final (CMP AG 10.2)", "build": cmp_airgun_final, "draw": True, "elim": True,
               "relay": False, "so_rule": "10.3.5", "so_s": 50, "events": "any"},
    "issf": {"book": "ISSF", "name": "ISSF 10m Air Rifle Final (ISSF 6.17.2)", "build": issf_final, "draw": True, "elim": True,
             "relay": False, "so_rule": "6.17.2 h", "so_s": 50, "events": "any"},
    "nra": {"book": "NRA", "name": "NRA Final (NRA 7.1.2)", "build": nra_final, "draw": False, "elim": False, "relay": False,
            "tie_places": 8, "so_rule": "7.1.2 q", "so_s": 75, "attention": True, "events": "any"},
}


def final_fits(kind: str, fmt: dict) -> bool:
    """Whether a final goes with a qualification format: the three-position Start-from-Zero final follows a 3x10 or
    3x20 (3PAR 10.3), the standing one a standing event (10.5)."""
    pos = {p["position"] for p in fmt["phases"] if p["kind"] in ("record", "stage") and p.get("position")}
    ev = FINAL_KINDS[kind]["events"]
    return ev == "any" or (ev == "3p" and {"kneeling", "prone"} <= pos) or (ev == "standing" and not pos & {"kneeling", "prone", "sitting"})

