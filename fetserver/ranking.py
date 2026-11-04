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
NRA-SB 15.7 (Smallbore Rifle 2026, also on electronic targets), whole rings:
  one position (15.7.1 a prone, 15.7.2 a): total, X's (center shots), each stage from the last (score, then X's), each
  bull from the last (prone: its score; other positions: score, then X's), then X's of higher value (14.3 e, g) and the
  stages again with those. Several positions (15.7.2 b): total, X's, standing, kneeling, prone totals (with X's), then
  stages from the last and bulls from the last, standing first, then kneeling, sitting, prone.
  Stages are the format's record phases in order; bulls are runs of the face's shots per bull (7.1).
Teams (ISSF 6.15.5, CMP 8.6.4, NRA 15.7): the members' results totalled and ranked like one competitor; here the
countback data is the members' shot and series values added position by position.
"""
from __future__ import annotations

from functools import cmp_to_key
from typing import List, Sequence

RULES = ("ISSF", "CMP", "NRA", "NRA-SB")
SB_POS = ("standing", "kneeling", "sitting", "prone")      # 15.7.2 b (3), (4): the order positions are compared in


def series(values: Sequence[int], n: int = 10) -> List[int]:
    return [sum(values[i:i + n]) for i in range(0, len(values), n)]


def summary(shots: Sequence[dict], layout: dict = None) -> dict:
    """The numbers every rule set needs, from one entry's counted shots. layout (matchformats.layout): the format's
    stages [(record shots, position)] and the face's shots per bull, for the NRA smallbore tie-breaks."""
    tenths = [s["tenths"] for s in shots]
    rings = [s["ring"] for s in shots]
    inner = [1 if s.get("inner") else 0 for s in shots]
    higher = [1 if s.get("inner") and s.get("higher") else 0 for s in shots]
    out = {"tenths": tenths, "rings": rings, "inner": inner, "total_tenths": sum(tenths), "total_rings": sum(rings),
           "inners": sum(inner), "shots": len(shots), "highers": sum(higher)}
    stages = (layout or {}).get("stages") or [(len(shots), "")]
    bull = max(1, (layout or {}).get("bull") or 5)
    # per stage: (score, X's, X's of higher value), and its bulls; per position (SB_POS order): totals and stages
    st, bulls, pos_tot = [], [], {p: [0, 0] for p in SB_POS}
    pos_st, pos_bulls = {p: [] for p in SB_POS}, {p: [] for p in SB_POS}
    i = 0
    for n, position in stages:
        part = range(i, min(i + n, len(shots)))
        i += n
        sc, xs, xh = sum(rings[k] for k in part), sum(inner[k] for k in part), sum(higher[k] for k in part)
        st.append([sc, xs, xh])
        bl = [[sum(rings[k] for k in part[j:j + bull]), sum(inner[k] for k in part[j:j + bull])] for j in range(0, len(part), bull)]
        bulls += bl
        if position in pos_tot:
            pos_tot[position][0] += sc
            pos_tot[position][1] += xs
            pos_st[position].append([sc, xs])
            pos_bulls[position] += bl
    out.update(stage=st, bulls=bulls, pos_total=[pos_tot[p] for p in SB_POS], pos_stages=[pos_st[p] for p in SB_POS],
               pos_bulls=[pos_bulls[p] for p in SB_POS], positions=len({p for _, p in stages if p}))
    return out


def _add(a, b):
    """Two countback values added: numbers, or lists of them added position by position (a longer list keeps its tail)."""
    if isinstance(a, list):
        return [_add(x, y) for x, y in zip(a, b)] + (a[len(b):] if len(a) > len(b) else b[len(a):])
    return a + b


def combine(summaries: Sequence[dict]) -> dict:
    """A team: totals added, and the countback lists added position by position (shot i of every member, ...)."""
    out = {}
    for s in summaries:
        for k, v in s.items():
            if k == "positions":
                out[k] = max(out.get(k, 0), v)
            else:
                out[k] = _add(out[k], v) if k in out else (list(map(_copy, v)) if isinstance(v, list) else v)
    for k in ("tenths", "rings", "inner", "total_tenths", "total_rings", "inners", "shots"):
        out.setdefault(k, [] if k in ("tenths", "rings", "inner") else 0)
    return out


def _copy(v):
    return list(map(_copy, v)) if isinstance(v, list) else v


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
    if rules == "NRA-SB":
        return _sb_key(s)
    counts = [sum(1 for r in s["rings"] if r == v) for v in range(10, -1, -1)]            # NRA: 10s, then 9s, ...
    return (s["total_rings"], back(series(s["rings"])), counts, s["inners"])


def _sb_key(s: dict) -> tuple:
    """NRA Smallbore 15.7 (see the module text). Summaries made without a layout count as one stage of 5-shot bulls."""
    back = lambda lst: list(reversed(lst))
    st = s.get("stage") or [[s["total_rings"], s["inners"], s.get("highers", 0)]]
    bulls = s.get("bulls") or [[r, 0] for r in s["rings"]]
    if s.get("positions", 0) <= 1:                                     # 15.7.1 a / 15.7.2 a: one position
        prone = s.get("pos_total") and s["pos_total"][SB_POS.index("prone")][0] == s["total_rings"] and s["total_rings"] > 0 \
            and not any(t[0] for i, t in enumerate(s["pos_total"]) if SB_POS[i] != "prone")
        bull_key = back([b[0] for b in bulls]) if prone else back(bulls)   # prone: the bull's score; otherwise with its X's
        return (s["total_rings"], s["inners"], back([t[:2] for t in st]), bull_key,
                s.get("highers", 0), back([[t[0], t[2]] for t in st]))
    pt = s["pos_total"]                                                # 15.7.2 b: several positions
    by = lambda name: pt[SB_POS.index(name)]
    return (s["total_rings"], s["inners"], [by("standing"), by("kneeling"), by("prone")],
            [back(x) for x in s["pos_stages"]], [back(x) for x in s["pos_bulls"]])


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
