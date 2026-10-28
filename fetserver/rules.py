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
