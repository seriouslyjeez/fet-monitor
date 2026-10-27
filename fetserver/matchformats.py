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
from typing import Dict, List

KINDS = {"setup", "sight", "pause", "record", "stage", "changeover"}
FIRE_KINDS = {"sight", "record", "stage"}
SCORING = {"decimal", "integer"}
POSITIONS = {"", "standing", "prone", "kneeling", "sitting"}
MAX_PHASES = 40
MAX_SECONDS = 4 * 3600
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def P(kind, label, minutes=0, seconds=0, *, shots=0, position="", warnings=(), start="", end="", rule=""):
    return {"kind": kind, "label": label, "seconds": int(minutes * 60 + seconds), "shots": shots, "position": position,
            "warnings": [{"at": int(a), "say": s} for a, s in warnings], "start": start, "end": end, "rule": rule}


def _issf_qual():
    return {
        "id": "issf-ar60", "name": "10m Air Rifle 60 shots (ISSF / USA Shooting qualification)", "rulebook": "ISSF", "tie_rules": "ISSF",
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
        "id": f"cmp-3x{n}", "name": f"3x{n} Three-Position Air Rifle (CMP / NFHS National Standard)", "rulebook": "CMP", "tie_rules": "CMP",
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
        "id": "cmp-s60", "name": "60 shots standing (CMP National Standard)", "rulebook": "CMP", "tie_rules": "CMP",
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
    w_s, w_r = [(30, "Thirty seconds")], [(300, "Five minutes remaining"), (120, "Two minutes remaining")]
    phases = [P("setup", "Call to the line", 5, rule="7.6", start="You may move your rifles and equipment to the firing line."),
              P("sight", "Preparation and sighting", 8, position="standing", warnings=w_s, rule="5.3.4, 7.8",
                start="Preparation and sighting stage. Time limit eight minutes. Start.", end="Stop.")]
    for i in range(3):
        if i:
            phases.append(P("sight", "Sighting", 5, position="standing", warnings=w_s, rule="5.3.4, 7.11",
                            start="Standing sighting stage. Time limit five minutes. Start.", end="Stop."))
        phases.append(P("pause", "Targets to match fire", 0, 30, rule="7.8"))
        phases.append(P("record", f"Standing match fire {i + 1} of 3", 25, shots=20, position="standing", warnings=w_r,
                        rule="5.3.4, 7.9", start="Standing match fire. 20 shots in a time limit of 25 minutes. Start.",
                        end="Stop. Unload."))
    return {"id": "cmp-s3x20", "name": "60 shots standing in three 20-shot stages (CMP, paper-target style)", "rulebook": "CMP", "tie_rules": "CMP",
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
    return {"id": f"nra-3x{n}", "name": f"3x{n} Precision Air Rifle (NRA)", "rulebook": "NRA", "tie_rules": "NRA",
            "edition": "NRA Precision Air Rifle Rules 2025", "scoring": "integer", "team_size": 4,
            "notes": "Prone, standing, kneeling (7.1). One clock per position: sighting time plus record time (8.1); "
                     "sighters until the shooter starts record, then record only (9.2 a). Position change: 15 minutes "
                     "recommended, never less than 5 (8.2 b). Team size is set by the match program.",
            "phases": phases}


def _nra_standing(n):
    return {"id": f"nra-s{n}", "name": f"{n} shots standing (NRA Precision Air Rifle)", "rulebook": "NRA", "tie_rules": "NRA",
            "edition": "NRA Precision Air Rifle Rules 2025", "scoring": "integer", "team_size": 4,
            "notes": "One clock: 8 minutes sighting plus record time (8.1, 8.2 a); sighters until the shooter starts record.",
            "phases": [_nra_stage("standing", n, True)]}


BUILTIN: Dict[str, dict] = {f["id"]: f for f in (
    _issf_qual(), _cmp_3p(10), _cmp_3p(20), _cmp_standing60(), _cmp_standing3x20(),
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
    if not any(p["kind"] in ("record", "stage") for p in out["phases"]):
        raise ValueError("a format needs at least one record or stage phase")
    return out


def builtin_list() -> List[dict]:
    return [copy.deepcopy(f) for f in BUILTIN.values()]
