"""Short plain-language summaries of the rules officials cite most (penalties, crossfires, protests), so a rule
reference on a results page or score card can be explained. Paraphrased, not quoted: the rulebook is the authority
(editions in docs/MATCH-FORMATS.md). A reference that is not in the table is printed as given."""
from __future__ import annotations

import re
from typing import Optional

SUMMARIES = {
    "ISSF": {
        "6.10.5": "A complaint about a sighting shot's value: the athlete may be moved; 2 points off if the target was right.",
        "6.10.9.2": "A target that cannot be repaired within 5 minutes: move to a reserve position, 5 minutes extra, unlimited sighters.",
        "6.10.9.3": "A shot that does not register: one more shot is fired and the target's record is examined.",
        "6.11.3.1": "Interrupted more than 3 minutes through no fault of the athlete: extra time.",
        "6.11.3.2": "Interrupted more than 5 minutes, or moved to another firing point: extra time and unlimited sighters.",
        "6.11.5": "Too many shots: the extra shots are annulled and 2 points are deducted for each one.",
        "6.11.6.1": "A crossfired competition shot is scored as a miss for the athlete who fired it.",
        "6.11.6.2": "A sighting shot crossfired onto another athlete's sighting target: no penalty.",
        "6.11.6.3": "A sighting shot crossfired onto another athlete's match target: 2 points off the firer's first series.",
        "6.11.6.4": "A confirmed crossfire that cannot be told apart: the athlete is credited with the highest undetermined shot.",
        "6.11.6.7": "A disclaimed shot the Range Officer confirms was not fired by the athlete is annulled.",
        "6.11.6.8": "A disclaimed shot that cannot be confirmed beyond doubt counts for the athlete.",
        "6.12.5.5": "Coaching on the firing line: a warning the first time, then 2 points off.",
        "6.12.6.1 a": "Open rule violation: a warning first, then 2 points off, then disqualification.",
        "6.12.6.1 b": "Deliberately concealed rule violation: disqualification.",
        "6.12.6.1 c": "Knowingly giving false information about an incident: 2 points off.",
        "6.12.6.3": "Serious safety violation: disqualification.",
        "6.16.5.2 c": "A protest of a shot value that is not upheld: 2 points off the disputed shot.",
    },
    "CMP": {
        "7.15.1": "No sighting shots during a Match Fire stage unless the Range Officer authorizes them; others are scored as misses.",
        "7.17.1": "A shot fired before START of a sighting stage: a miss on the first competition shot.",
        "7.17.2": "A shot after STOP: 2 points off (after sighting) or a miss on the last competition shot (after match fire).",
        "7.17.6": "Match shots not fired in time are scored as misses.",
        "7.17.8": "Too many shots in a position: the extra shots are annulled and 2 points are deducted for each one.",
        "7.17.9": "A crossfired match shot is scored as a miss for the athlete who fired it.",
        "7.17.10": "A disclaimed shot is annulled if the Range Officer can confirm it was not the athlete's; otherwise it counts.",
        "7.17.11": "Double loading: the higher shot counts, the lower one is annulled.",
        "7.18.3": "Moved to another target or relay: the time left plus 2 minutes, and unlimited sighters.",
        "7.22.1": "Open rule violation: a warning first, then 2 points off, then disqualification.",
        "7.22.2": "Concealed rule violation: disqualification by at least two match officials.",
        "7.22.3": "Dangerous rifle handling: disqualification by at least two match officials.",
        "8.4 c": "A protest of a shot value that is not upheld: 2 points off.",
    },
    "NRA": {
        "9.2 d": "A sighting shot after the first record shot (unless the Jury allows it): 2 points off each.",
        "9.30": "Penalties for rule violations: a warning, a deduction of points, or disqualification.",
        "10.10.4 a": "A crossfired competition shot is a miss; a sighter on another competitor's record target: 2 points off the firer's first series.",
        "10.10.4 c": "A confirmed crossfire that cannot be told apart: credited with the highest undetermined shot.",
        "10.10.4 f": "A disclaimed shot the Range Officer confirms was not fired by the competitor is annulled.",
        "16.1": "Challenges of a score: within the challenge time set by the match.",
    },
}
BOOKS = {"ISSF": "ISSF", "USAS": "ISSF", "USA": "ISSF", "CMP": "CMP", "NFHS": "CMP", "NRA": "NRA"}
REF_RE = re.compile(r"^\s*(?:(ISSF|USAS|USA|CMP|NFHS|NRA)\b\s*)?(?:rule\s*)?(\d+(?:\.\d+)*)\s*\(?\s*([a-z])?\s*\)?\s*$", re.I)


def explain(ref: str, default_book: str = "ISSF") -> dict:
    """{"ref": the reference as given, "text": a summary or None, "book": the rulebook it was looked up in}. With a
    letter not in the table, the rule without it is used (6.11.6.3 b -> 6.11.6.3) when that is listed."""
    m = REF_RE.match(ref or "")
    if not m:
        return {"ref": ref, "text": None, "book": None}
    book = BOOKS.get((m.group(1) or "").upper(), default_book if default_book in SUMMARIES else "ISSF")
    num, letter = m.group(2), (m.group(3) or "").lower()
    table = SUMMARIES[book]
    for key in ([f"{num} {letter}"] if letter else []) + [num]:
        if key in table:
            return {"ref": ref, "text": table[key], "book": book}
    return {"ref": ref, "text": None, "book": book}


def lookup_text(ref: Optional[str], default_book: str = "ISSF") -> Optional[str]:
    return explain(ref or "", default_book)["text"]


# ------------------------------------------------------------------------------------------- decision guidance
RULEBOOK_URL = {"ISSF": "https://issf-sports.org/rules", "CMP": "https://thecmp.org/air/national-standard-three-position-air-rifle-rules/",
                "NRA": "http://rulebooks.nra.org"}
NRA_EARLY_LATE = ("NRA 14.5", "Early or late shots: the highest-value shots, one for each shot fired in error, are scored as misses.",
                  None, "Ignore this shot, then on Shots… override the competitor's highest-value record shot to a miss.")
# (code, phase kind or None, stream) -> {book: (rule, what it says, suggested decision, what to do when no decision fits)}
_BEFORE = {"ISSF": ("ISSF 6.11.1.1 h", "A shot before START of the preparation and sighting time: the first competition shot is a miss.", "miss_first", None),
           "CMP": ("CMP 7.17.1", "A shot before START of a sighting stage: a miss on the first competition shot.", "miss_first", None),
           "NRA": NRA_EARLY_LATE}
_BETWEEN = {"ISSF": ("ISSF 6.11.1.1 k", "After the sighting STOP and before MATCH FIRING START: not a match shot, and 2 points off the first competition shot.", "penalty2_first", None),
            "CMP": ("CMP 7.17.2", "After the sighting STOP and before the match START: 2 points off the first competition shot.", "penalty2_first", None),
            "NRA": NRA_EARLY_LATE}
_AFTER_SIGHTER = {"ISSF": ("ISSF 6.11.1.3 a", "A shot after STOP is scored as a miss. Shots not fired in time already count as misses, so ignoring it gives that result.", "ignore", None),
                  "CMP": ("CMP 7.17.2", "A shot after the match STOP is scored as a miss. Shots not fired in time already count as misses, so ignoring it gives that result.", "ignore", None),
                  "NRA": NRA_EARLY_LATE}
_AFTER_RECORD = {"ISSF": ("ISSF 6.11.1.3 a", "A shot after STOP is scored as a miss.", "miss", None),
                 "CMP": ("CMP 7.17.2", "A shot after the match STOP is scored as a miss.", "miss", None),
                 "NRA": ("NRA 14.5", NRA_EARLY_LATE[1], "count", "Count this shot, then on Shots… override the competitor's highest-value record shot to a miss.")}
_CHANGEOVER = {"ISSF": _AFTER_SIGHTER["ISSF"],
               "CMP": ("CMP 7.17.2", "A shot after the STOP of a match fire stage is scored as a miss on the last competition shot of that stage.", None,
                       "Ignore this shot, then on Shots… override the last record shot of the stage just fired to a miss."),
               "NRA": NRA_EARLY_LATE}
_EXTRA_SIGHTER = {"ISSF": ("ISSF 6.11.1.2 d", "Sighting shots after match firing started are scored as misses in the competition.", None,
                           "Ignore this shot, then on Shots… add a shot (a miss) after the record shot fired just before it."),
                  "CMP": ("CMP 7.15.1", "Unauthorized sighting shots during a match fire stage are scored as misses.", None,
                          "Ignore this shot, then on Shots… add a shot (a miss) after the record shot fired just before it."),
                  "NRA": ("NRA 9.2 d", "A sighting shot after the first record shot (unless the Jury allows it): 2 points off.", "penalty2_first", None)}
_TOO_MANY = {"ISSF": ("ISSF 6.11.5", "Too many shots: the extra shots are annulled and 2 points are taken off the lowest shot of the first series for each one.",
                      "annul_penalty2", None),
             "CMP": ("CMP 7.17.8", "Too many shots in a position: the extra shots are annulled and 2 points are taken off the lowest shot for each one.",
                     "annul_penalty2", None),
             "NRA": ("NRA 10.10.4 d", "More hits than the program allows, when nobody else can be shown to have fired them: the highest-value hits are nullified.", "annul",
                     "Annul the highest-value record shot instead if that is not this one.")}
_PAUSED = {"ISSF": ("ISSF 6.11.1.3 a", "A shot after a STOP is scored as a miss.", "miss", None),
           "CMP": ("CMP 7.17.3", "A shot fired by mistake after an emergency STOP: a warning; a match shot counts (more shots after that: misses).", "count", None),
           "NRA": (None, "No specific rule: the match director decides.", None, None)}


def guidance(code, kind, stream: str, book: str) -> dict:
    """What the rulebook says about a flagged shot: {rule, text, suggest (a decision, or None), todo, url}."""
    book = book if book in RULEBOOK_URL else "ISSF"
    rec = stream == "scored"
    table = None
    if code in ("before_start",) or (code in ("before_cmd", "no_fire") and kind in ("sight", "stage", "setup")):
        table = _BEFORE
    elif code in ("before_cmd", "no_fire") and kind in ("record", "pause"):
        table = _BETWEEN
    elif code == "no_fire" and kind == "changeover":
        table = _CHANGEOVER
    elif code in ("after_end", "after_own_time"):
        table = _AFTER_RECORD if rec else _AFTER_SIGHTER
    elif code in ("sighter_in_record", "sighter_after_record"):
        table = _EXTRA_SIGHTER
    elif code == "too_many":
        table = _TOO_MANY
    elif code == "paused":
        table = _PAUSED if rec else {b: (None, "A sighting shot while the clock was stopped does not count.", "ignore", None) for b in RULEBOOK_URL}
    if table is None:                                  # restarted, moving, a record shot during sighting: no rule says it
        why = {"restarted": "The match director restarted this part of the match: shots from before the restart normally do not count.",
               "moving": "Fired while the shooter was moving to another target (their time was stopped).",
               "record_in_sight": "A record shot during sighting time: sighting time is for sighters.",
               "waiting": "Fired before this shooter's part of the match on this relay began (CMP 7.18): it does not count.",
               "not_in_shoot_off": "Fired during a shoot-off this finalist is not part of (CMP 10.2.9 n): it does not count."}.get(code, "The match director decides.")
        rule, text, suggest, todo = None, why, ("annul" if rec else "ignore"), None
    else:
        rule, text, suggest, todo = table[book]
        if suggest in ("miss_first", "penalty2_first", "ignore") and rec:         # a record shot: the matching record decision
            suggest = {"miss_first": "miss", "penalty2_first": "penalty2", "ignore": "annul"}[suggest]
        elif suggest in ("miss", "count", "annul", "penalty2", "annul_penalty2") and not rec:
            suggest = {"miss": "miss_first", "penalty2": "penalty2_first"}.get(suggest, "ignore")
    return {"rule": rule, "text": text, "suggest": suggest, "todo": todo, "url": RULEBOOK_URL[book]}
