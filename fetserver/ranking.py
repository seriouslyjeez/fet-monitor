"""Ranking with each rulebook's tie-breaking rules (docs/MATCH-FORMATS.md has the sources).

An entry (a competitor, or a team as the sum of its members) is described by its counted record shots in firing order,
each {"tenths": decimal value x10 after penalties, "ring": whole-ring value after penalties, "inner": inner ten}.
rank() sorts entries best first and gives places with ties sharing a place (1, 1, 3): an unbreakable tie lists the tied
entries alphabetically by family name (ISSF 6.15.1 e, NRA 15.3 d/e), CMP 8.6.1 d allows duplicate awards (same place).

ISSF 6.15.1 (2026 Rule Book):
  decimal (10 m air rifle, f): total, last 10-shot series backwards (decimal), shot-by-shot backwards (decimal).
  integer (a-d): total, inner tens, last 10-shot series backwards (whole rings), shot-by-shot backwards with an inner
  ten above a ten, then shot-by-shot backwards in decimals (electronic targets).
CMP 8.6.1 (National Standard 3PAR, 16th ed.):
  decimal (e): as ISSF decimal.
  integer (a-c): total, inner tens, 10-shot series backwards (score, then that series' inner tens), shot-by-shot
  backwards with an inner ten above a ten.
NRA 15.3 (Precision Air Rifle 2025): total, last 10-shot string backwards, most 10s then 9s then 8s ..., most inner 10s.
Teams (ISSF 6.15.5, CMP 8.6.4, NRA 15.7): the members' results totalled and ranked like one competitor; here the
countback data is the members' shot and series values added position by position.
"""
from __future__ import annotations

from functools import cmp_to_key
from typing import List, Sequence

RULES = ("ISSF", "CMP", "NRA")


def series(values: Sequence[int], n: int = 10) -> List[int]:
    return [sum(values[i:i + n]) for i in range(0, len(values), n)]


def summary(shots: Sequence[dict]) -> dict:
    """The numbers every rule set needs, from one entry's counted shots."""
    tenths = [s["tenths"] for s in shots]
    rings = [s["ring"] for s in shots]
    inner = [1 if s.get("inner") else 0 for s in shots]
    return {"tenths": tenths, "rings": rings, "inner": inner, "total_tenths": sum(tenths), "total_rings": sum(rings),
            "inners": sum(inner), "shots": len(shots)}


def combine(summaries: Sequence[dict]) -> dict:
    """A team: totals added, and the countback lists added position by position (shot i of every member, ...)."""
    out = {"tenths": [], "rings": [], "inner": [], "total_tenths": 0, "total_rings": 0, "inners": 0, "shots": 0}
    for s in summaries:
        for k in ("tenths", "rings", "inner"):
            lst = out[k]
            for i, v in enumerate(s[k]):
                if i < len(lst):
                    lst[i] += v
                else:
                    lst.append(v)
        for k in ("total_tenths", "total_rings", "inners", "shots"):
            out[k] += s[k]
    return out


def sort_key(s: dict, rules: str, decimal: bool) -> tuple:
    """Bigger is better, compared element by element."""
    back = lambda lst: list(reversed(lst))
    if rules not in RULES:
        raise ValueError(f"tie-break rules must be one of {RULES}")
    if decimal and rules in ("ISSF", "CMP"):
        return (s["total_tenths"], back(series(s["tenths"])), back(s["tenths"]))
    if rules == "ISSF":
        weighted = [r + (0.5 if i else 0) for r, i in zip(s["rings"], s["inner"])]       # an inner ten outranks a ten
        return (s["total_rings"], s["inners"], back(series(s["rings"])), back(weighted), back(s["tenths"]))
    if rules == "CMP":
        ser = list(zip(series(s["rings"]), series(s["inner"])))                           # score, then its inner tens
        weighted = [r + (0.5 if i else 0) for r, i in zip(s["rings"], s["inner"])]
        return (s["total_rings"], s["inners"], back(ser), back(weighted))
    counts = [sum(1 for r in s["rings"] if r == v) for v in range(10, -1, -1)]            # NRA: 10s, then 9s, ...
    return (s["total_rings"], back(series(s["rings"])), counts, s["inners"])


def _cmp(a, b) -> int:
    """Compare two keys, bigger is better, element by element (a longer list wins when one is a prefix of the other)."""
    if isinstance(a, (list, tuple)):
        for x, y in zip(a, b):
            c = _cmp(x, y)
            if c:
                return c
        return (len(a) > len(b)) - (len(a) < len(b))
    return (a > b) - (a < b)


def rank(entries: List[dict], rules: str, decimal: bool) -> List[dict]:
    """entries: {"summary": summary(...), "sort_name": family name for alphabetical listing, ...}. Returns them best
    first with "place" (ties share a place; the next place skips) and "tied" (True when an unbreakable tie remains).
    An entry with "dq" set (disqualified) gets no place and is listed after the others."""
    dq = [dict(e, place=None, tied=False) for e in entries if e.get("dq")]          # disqualified: no place, listed last
    entries = [e for e in entries if not e.get("dq")]
    keyed = [(sort_key(e["summary"], rules, decimal), e) for e in entries]

    def order(x, y):
        c = _cmp(y[0], x[0])                              # best first
        if c:
            return c
        nx, ny = (x[1].get("sort_name") or "").casefold(), (y[1].get("sort_name") or "").casefold()
        return (nx > ny) - (nx < ny)                     # an unbreakable tie: alphabetical by family name

    keyed.sort(key=cmp_to_key(order))
    out = []
    for i, (k, e) in enumerate(keyed):
        same = i > 0 and _cmp(keyed[i - 1][0], k) == 0
        e = dict(e, place=out[-1]["place"] if same else i + 1, tied=False)
        if same:
            e["tied"] = True
            out[-1]["tied"] = True
        out.append(e)
    return out + sorted(dq, key=lambda e: (e.get("sort_name") or "").casefold())
