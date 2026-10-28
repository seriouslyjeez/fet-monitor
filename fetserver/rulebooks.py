"""One table per rulebook: every procedure and rule reference the match engine and the screens use, so a format, its
finals and its procedures never borrow from another book (docs/MATCH-FORMATS.md has the editions and sources).

A format names its book ("book": one of BOOKS). Formats saved before books existed only have "rulebook": "ISSF", "CMP"
(the National Standard 3PAR rules), "CMP Air Gun", "NRA" or "Custom" (no book: NONE). A procedure the book does not
cover stays available, with no rule cited: the match director decides (the user's decision, 2026-10-28).
"""
from __future__ import annotations

from typing import Optional

ISSF, AG, PAR, NRA, NONE = "ISSF", "CMP-AG", "CMP-3PAR", "NRA", "NONE"
AG_EDITION = "CMP Air Rifle and Air Pistol Competition Rules, 5th ed. (2026)"
PAR_EDITION = "CMP National Standard 3PAR Rules, 16th ed. (2026-2028)"
BOOKS = {
    ISSF: {"name": "ISSF", "title": "ISSF Rule Book 2026 (USA Shooting follows it)", "cite": "ISSF",
           "url": "https://issf-sports.org/rules", "tie": "ISSF"},
    AG: {"name": "CMP Air Gun", "title": AG_EDITION, "cite": "CMP AG",
         "url": "https://thecmp.org/competitions/cmp-competitions-rulebooks/", "tie": "CMP"},     # 8.5.1: the same countback as 3PAR 8.6.1
    PAR: {"name": "CMP 3PAR", "title": PAR_EDITION, "cite": "CMP 3PAR",
          "url": "https://thecmp.org/air/national-standard-three-position-air-rifle-rules/", "tie": "CMP"},
    NRA: {"name": "NRA", "title": "NRA Precision Air Rifle Rules 2025", "cite": "NRA", "url": "http://rulebooks.nra.org", "tie": "NRA"},
    NONE: {"name": "No rulebook", "title": "No rulebook: the match director decides", "cite": "", "url": "", "tie": None},
}
LEGACY = {"ISSF": ISSF, "CMP": PAR, "CMP 3PAR": PAR, "CMP Air Gun": AG, "NRA": NRA}
TIE_RULES = ("ISSF", "CMP", "NRA")


def book_of(fmt: dict) -> str:
    b = fmt.get("book")
    return b if b in BOOKS else LEGACY.get(fmt.get("rulebook"), NONE)


def tie_of(fmt: dict) -> str:
    """The tie-break rules: the book's; with no book, the ones the format chose (ISSF if none)."""
    t = BOOKS[book_of(fmt)]["tie"] or fmt.get("tie_rules")
    return t if t in TIE_RULES else "ISSF"


def cite(book: str, num: Optional[str]) -> Optional[str]:
    return f"{BOOKS[book]['cite']} {num}" if num and book != NONE else None


def director(book: str) -> str:
    return "No rulebook: the match director decides." if book == NONE else f"Not in the {BOOKS[book]['name']} rules: the match director decides."


# ------------------------------------------------------------------------------------------- per-book procedures
# Each entry: the rule number in that book (None: not covered) and what it gives.
PROC = {
    ISSF: {
        "move": (300, "6.10.9.2, 6.11.3.2", "their time stops while they move; when they are ready they get 5 minutes extra and unlimited sighting shots before they go on with record."),
        "later_relay": None,
        "too_many": "6.11.5",
        "not_registered": "6.10.9.3", "not_registered_move": "6.10.9.4",
        "double_load": "6.11.2.4 b", "not_upheld": "6.16.5.2 c", "shot_protest": "6.16.5.2", "disturbance": "6.11.7",
        "crossfire": "6.11.6", "sighter_crossfire": "6.11.6.3",
        "penalty_example": "6.12.6.1 a", "dq": "6.12.6", "late": "6.11.4", "protest_min": (10, "6.16.5.1"),
        "incident": "6.10.4 h", "best_count": None, "finals": (),
        "malfunction": "6.13.4", "malfunction_allowable": "6.13.2.1", "range": "6.10.9.1, 6.11.3.2",
    },
    AG: {
        "move": (120, "7.12.2", "their time stops while they move; when they are ready they get the time they had left plus 2 minutes, and unlimited sighting shots before they go on with record."),
        "later_relay": (120, "7.12, 7.12.2"),
        "too_many": "7.11.8",
        "not_registered": "8.2.3 a", "not_registered_move": "8.2.3 a",
        "double_load": "7.11.4 b", "not_upheld": "8.2.3 c", "shot_protest": "8.2.3", "disturbance": None,
        "crossfire": "7.11.9", "sighter_crossfire": None,
        "penalty_example": "9.2 b", "dq": "9.2 c", "late": "7.14", "protest_min": (10, "8.1.9"),
        "incident": "7.11 (RIF)", "best_count": None, "finals": ("airgun",),
        "malfunction": "7.13", "malfunction_allowable": None, "range": "7.12.3",
    },
    PAR: {
        "move": (120, "7.18.3", "their time stops while they move; when they are ready they get the time they had left plus 2 minutes, and unlimited sighting shots before they go on with record."),
        "later_relay": (120, "7.18, 7.18.3"),
        "too_many": "7.17.8",
        "not_registered": "8.4 a", "not_registered_move": "8.4 a",
        "double_load": "7.17.11", "not_upheld": "8.4 c", "shot_protest": "8.4", "disturbance": None,
        "crossfire": "7.17.9", "sighter_crossfire": None,
        "penalty_example": "7.22.1", "dq": "7.22", "late": "7.20", "protest_min": (10, "8.2"),
        "incident": "6.5 (Range Incident Form)", "best_count": "3.2.2 c", "finals": ("standard",),
        "malfunction": "7.19.1", "malfunction_allowable": None, "range": "7.18.4",
    },
    NRA: {
        "move": (300, "10.10.5 c, 9.2 b", "their time stops while they move; when they are ready they get 5 minutes extra and unlimited sighting shots before they go on with record."),
        "later_relay": None,
        "too_many": "10.10.4 d",
        "not_registered": "10.10.5 d", "not_registered_move": "10.10.5 d 3",
        "double_load": None, "not_upheld": None, "shot_protest": None, "disturbance": None,
        "crossfire": "10.10.4", "sighter_crossfire": "10.10.4 a",
        "penalty_example": "9.30", "dq": "9.30", "late": None, "protest_min": (None, "16.1 b"),
        "incident": "Range Officer's Notation", "best_count": None, "finals": (),
        "malfunction": "9.5", "malfunction_allowable": None, "range": "9.2 b, 10.3.2",
    },
    NONE: {
        "move": (0, None, "their time stops while they move; when they are ready, the match director gives any extra time (Extra time…)."),
        "later_relay": (0, None),
        "too_many": None,
        "not_registered": None, "not_registered_move": None,
        "double_load": None, "not_upheld": None, "shot_protest": None, "disturbance": None,
        "crossfire": None, "sighter_crossfire": None,
        "penalty_example": None, "dq": None, "late": None, "protest_min": (None, None),
        "incident": None, "best_count": None, "finals": ("standard", "airgun"),     # a final keeps its own book's rules
        "malfunction": None, "malfunction_allowable": None, "range": None,
    },
}
FINAL_NAMES = {"standard": "CMP Standard Final (3PAR 10.4)", "airgun": "CMP Air Gun Final (Air Gun 10.2)"}


def rule(book: str, key: str) -> Optional[str]:
    """The cited rule for a procedure in this book ("CMP AG 7.11.8"), or None when the book does not cover it."""
    v = PROC[book][key]
    return cite(book, v if isinstance(v, str) else None)


def move_extra(book: str) -> tuple:
    """(extra seconds, cited rule or None, what the shooter gets) after a move to another target."""
    s, num, text = PROC[book]["move"]
    return s, cite(book, num), text


def later_relay(book: str) -> tuple:
    """(extra ms on the later relay, cited rule or None) for a shooter who finishes on a later relay: the CMP books
    cover it (3PAR 7.18.3, Air Gun 7.12.2: the time left plus 2 minutes); elsewhere the match director decides."""
    v = PROC[book]["later_relay"]
    return (v[0] * 1000, cite(book, v[1])) if v else (0, None)


def protest_minutes(book: str) -> Optional[int]:
    """Minutes of protest time after preliminary results; None: the challenge time set for the competition."""
    return PROC[book]["protest_min"][0]


def finals(book: str) -> tuple:
    return PROC[book]["finals"]


def interruption_time(book: str, reason: str, left_ms: int, lost_s: int) -> tuple:
    """(time left, sighters?, rule) for a shooter whose interruption (or NRA disabled rifle) ended after lost_s seconds;
    left_ms: what they had left when it began (their clock stood still since).
    ISSF 6.11.3.1: over 3 minutes, the time lost (in the last 5 minutes: the time left + 1 minute); up to 3 minutes the
    time lost is not given back; 6.11.3.2: over 5 minutes, + 5 minutes and unlimited sighters.
    CMP Air Gun 7.12.1-2: over 1 minute the time lost; over 5 minutes + 2 minutes and unlimited sighters.
    CMP 3PAR 7.18.1-2: the time lost; over 5 minutes + 2 minutes and unlimited sighters.
    NRA 9.2 b: over 3 minutes equal time, over 5 unlimited sighters; 9.5 (disabled rifle): the time that remained, sighters.
    No rulebook: the time lost is given back; the match director adds anything else."""
    if reason == "malfunction":                              # only NRA stops the clock for a malfunction
        return left_ms, True, "NRA 9.5"
    if book == PAR:
        return (left_ms + 120_000, True, "CMP 3PAR 7.18.2") if lost_s > 300 else (left_ms, False, "CMP 3PAR 7.18.1")
    if book == AG:
        if lost_s > 300:
            return left_ms + 120_000, True, "CMP AG 7.12.2"
        return (left_ms, False, "CMP AG 7.12.1") if lost_s > 60 else (max(0, left_ms - lost_s * 1000), False, "CMP AG 7.12.1")
    if book == NRA:
        back = left_ms if lost_s > 180 else max(0, left_ms - lost_s * 1000)
        return back, lost_s > 300, "NRA 9.2 b"
    if book == NONE:
        return left_ms, False, None
    if lost_s <= 180:
        return max(0, left_ms - lost_s * 1000), False, "ISSF 6.11.3.1"
    back = left_ms + (60_000 if left_ms <= 300_000 else 0)
    return (back + 300_000, True, "ISSF 6.11.3.2") if lost_s > 300 else (back, False, "ISSF 6.11.3.1")


def range_interruption(book: str, lost_s: int) -> tuple:
    """(extra seconds, sighters?, rule) for the whole range after an interruption of lost_s seconds (the clock was
    stopped, so the time lost is given back): ISSF 6.10.9.1 / 6.11.3.2 over 5 minutes + 5 minutes and sighters;
    CMP Air Gun 7.12.3 and CMP 3PAR 7.18.4 over 5 minutes + 2 minutes and sighters; NRA 10.3.2 over 5 minutes
    sighters; no rulebook: nothing more (the match director decides)."""
    over = lost_s > 300
    if book == ISSF:
        return (300, True, "ISSF 6.10.9.1, 6.11.3.2") if over else (0, False, "ISSF 6.11.3.1")
    if book in (AG, PAR):
        return (120 if over else 0), over, rule(book, "range")
    if book == NRA:
        return 0, over, "NRA 10.3.2" if over else "NRA 9.2 b"
    return 0, False, None


def malfunction(book: str, left_ms: int, allowable: bool) -> tuple:
    """(sighters?, extra seconds, rule) for a malfunction while the clock keeps running (NRA stops it instead, 9.5):
    ISSF 6.13.4: sighters only for an allowable malfunction, no extra time; CMP Air Gun 7.13 and 3PAR 7.19.1:
    sighters, and 2 minutes extra when it happens in the last 5 minutes; no rulebook: sighters if the range officer
    allows them, no extra time."""
    if book in (AG, PAR):
        return True, (120 if left_ms <= 300_000 else 0), rule(book, "malfunction")
    return allowable, 0, rule(book, "malfunction")


def texts(book: str) -> dict:
    """What the screens say about each procedure, for this book only."""
    b, r = BOOKS[book], (lambda k: rule(book, k))
    d = director(book)
    s, mv, mtext = move_extra(book)
    lr_ms, lr = later_relay(book)
    pm, pnum = PROC[book]["protest_min"]
    nr, nrm = r("not_registered"), r("not_registered_move")
    return {
        "book": book, "name": b["name"], "title": b["title"], "url": b["url"],
        "move": (mv + ": " if mv else "") + mtext,
        "move_extra_s": s,
        "later_relay": lr,
        "later_relay_text": ((lr + ": their shots so far stay on this relay. On the later relay they wait until this stage, then have the time they have "
                              "left now plus 2 minutes, unlimited sighters, and only the record shots they still owe; then the rest of the match.")
                             if lr else d + " Their shots so far stay on this relay; on the later relay they wait until this stage, then have the time "
                             "they have left now and only the record shots they still owe (add extra time if the match director gives it).") +
                            " The results join both parts.",
        "late": (r("late") + ": a" if r("late") else d + " A") + " late shooter may start but gets no extra time; sighters within their time, then Start record.",
        "not_registered": (nr + ": t" if nr else d + " T") + "he shooter fires one more match shot now. After the stage the match director settles it: "
                          "found (its value counts) or not found (a miss); either way the last shot of the stage, the extra one, is annulled.",
        "not_registered_move": "The extra shot did not register either: move this shooter to another target" + (f" ({nrm})" if nrm else "") +
                               "; the two shots are fired again there.",
        "disturbance": (r("disturbance") + ": i" if r("disturbance") else d + " I") +
                       "f the match director finds it justified, the shot is annulled and may be fired again; if not, it counts, with no penalty.",
        "double_load": "The lower of the two is annulled" + (f" ({r('double_load')})." if r("double_load") else ". " + d),
        "shot_protest": r("shot_protest") + ": a shot-value protest is made before the next shot or within 3 minutes of the last one."
                        if r("shot_protest") else d,
        "not_upheld": r("not_upheld"),
        "crossfire": "It is annulled here. A record shot is scored as a miss for the firing point that fired it" +
                     ("; a sighter on a record target costs that firing point 2 points" if r("sighter_crossfire") else "; a sighter has no penalty") +
                     (f" ({r('crossfire')})." if r("crossfire") else ". " + d),
        "penalty_example": r("penalty_example") or "the rule or reason",
        "dq_rule": r("dq"),
        "dq": (r("dq") + ": " if r("dq") else "") + "a disqualification is decided by at least two match officials. Scores are cleared and DQ is shown.",
        "protest_time": f"{cite(book, pnum)}: {pm} minutes" if pm else (cite(book, pnum) + ": the challenge time set for the competition" if pnum else
                                                                       d + " The protest time is the challenge time set for the competition."),
        "challenge": pm is None,
        "incident": r("incident"),
        "best_count": r("best_count"),
        "interrupt": "This shooter's time stops" + (f" ({b['cite']} " + {ISSF: "6.11.3", AG: "7.12", PAR: "7.18", NRA: "9.2 b"}[book] + ")" if book != NONE else ""),
        "malfunction": {ISSF: "ask_allowable", NRA: "stops_clock"}.get(book, "sighters"),
        "malfunction_text": {ISSF: "Is it an ALLOWABLE malfunction (ISSF 6.13.2.1)? OK: yes, sighters allowed after the repair. Cancel: no (no sighters). No extra time either way (6.13.4).",
                             AG: "Malfunction (CMP AG 7.13): sighters within the time left; 2 minutes extra if it happened in the last 5 minutes. Continue?",
                             PAR: "Malfunction (CMP 3PAR 7.19.1): sighters on request; 2 minutes extra if it happened after the 5-minute warning. Continue?",
                             NRA: "Disabled rifle (NRA 9.5): the shooter's time stops until you press Repaired; then the time that remained and sighters.",
                             NONE: d + " Allow sighters after the repair? OK: yes. Cancel: no. No extra time (use Extra time… if the match director gives it)."}[book],
        "range_interrupt": "Interrupt the whole range now? Every clock stops. When it ends, the time is given back" +
                           (f"; over 5 minutes the {b['name']} rules add " + {ISSF: "5 minutes and sighters (6.10.9.1).", AG: "2 minutes and sighters (7.12.3).",
                                                                             PAR: "2 minutes and sighters (7.18.4).", NRA: "sighters (10.3.2)."}[book]
                            if book != NONE else ". " + d),
        "finals": list(finals(book)),
    }
