"""Sight adjustment for practice: where the group's centre is, which shots are flyers, how far the impact must move.

Positions are shot centres in integer hundredths of a millimetre, y up (the scoring coordinates).

* Aim point = the geometric median of the kept shots (the point with the least total distance to them). The
  decimal score drops by 0.1 for every 0.25 mm of distance from the centre, on every ring, so moving that point
  onto the bullseye gives the highest decimal total for the group; the average (mean point of impact) is pulled
  towards loose shots and is reported for comparison only.
* Flyers: measured from the geometric median of ALL the shots, a shot more than FLYER_FACTOR times the median
  distance (but at least FLYER_MIN_H) away is a flyer, farthest first, as long as MIN_KEPT shots remain.
  Misses have no position and are left out.
* The correction is the move the IMPACT needs: (-centre). Converting to clicks (whole clicks only) is done by the
  page, because clicks per ring is a per-browser setting; one ring is RING_MM wide.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

MIN_SHOTS = 3          # shots on paper needed for a suggestion (and kept after removing flyers)
MIN_KEPT = 3
FLYER_FACTOR = 3.0     # x the median distance from the centre (2.5 flagged a shot in ~27% of normal 5-10 shot groups)
FLYER_MIN_H = 150      # 1.5 mm: never call a shot a flyer when it is this close, however tight the group
RING_MM = 2.5          # ISSF 10 m air rifle: every ring is 2.5 mm wide (radius step)

Pt = Tuple[int, int]


def geometric_median(points: Sequence[Tuple[float, float]], tol: float = 1e-4, max_iter: int = 2000) -> Tuple[float, float]:
    """Weiszfeld's iteration with the Vardi-Zhang step, so a data point that is itself the median is handled."""
    n = len(points)
    if n == 0:
        raise ValueError("no points")
    x = sum(p[0] for p in points) / n
    y = sum(p[1] for p in points) / n
    for _ in range(max_iter):
        wx = wy = wsum = 0.0
        rx = ry = 0.0
        coincident = 0
        for px, py in points:
            d = math.hypot(px - x, py - y)
            if d < 1e-9:
                coincident += 1
                continue
            w = 1.0 / d
            wx += w * px
            wy += w * py
            wsum += w
            rx += w * (px - x)
            ry += w * (py - y)
        if wsum == 0.0:                                   # every point is here
            return x, y
        tx, ty = wx / wsum, wy / wsum
        if coincident:
            r = math.hypot(rx, ry)
            if r <= coincident:                           # the current data point is the median
                return x, y
            g = coincident / r
            tx, ty = (1 - g) * tx + g * x, (1 - g) * ty + g * y
        step = math.hypot(tx - x, ty - y)
        x, y = tx, ty
        if step < tol:
            break
    return x, y


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def find_flyers(points: Sequence[Pt]) -> List[int]:
    """Indexes of the flyers in points (farthest first)."""
    if len(points) <= MIN_KEPT:
        return []
    cx, cy = geometric_median([(float(x), float(y)) for x, y in points])
    dist = [math.hypot(x - cx, y - cy) for x, y in points]
    limit = max(FLYER_FACTOR * _median(dist), FLYER_MIN_H)
    far = sorted((i for i, d in enumerate(dist) if d > limit), key=lambda i: -dist[i])
    return far[:len(points) - MIN_KEPT]


def suggest(shots: Sequence[dict]) -> dict:
    """shots: [{"n": shot number, "x_h": int|None, "y_h": int|None, "miss": bool}] in firing order."""
    on_paper = [s for s in shots if not s.get("miss") and s.get("x_h") is not None and s.get("y_h") is not None]
    out = {"shots": len(shots), "on_paper": len(on_paper), "misses": len(shots) - len(on_paper),
           "min_shots": MIN_SHOTS, "ring_mm": RING_MM, "ready": len(on_paper) >= MIN_SHOTS,
           "kept": [s["n"] for s in on_paper], "flyers": [], "points": []}
    if not out["ready"]:
        return out
    pts = [(int(s["x_h"]), int(s["y_h"])) for s in on_paper]
    fly = set(find_flyers(pts))
    kept = [p for i, p in enumerate(pts) if i not in fly]
    cx, cy = geometric_median([(float(x), float(y)) for x, y in kept])
    mx = sum(p[0] for p in kept) / len(kept)
    my = sum(p[1] for p in kept) / len(kept)
    mm = lambda h: round(h / 100.0, 3)
    out.update({
        "kept": [s["n"] for i, s in enumerate(on_paper) if i not in fly],
        "flyers": [s["n"] for i, s in enumerate(on_paper) if i in fly],
        "points": [{"n": s["n"], "x_mm": mm(p[0]), "y_mm": mm(p[1]), "flyer": i in fly} for i, (s, p) in enumerate(zip(on_paper, pts))],
        "center_x_mm": mm(cx), "center_y_mm": mm(cy),               # aim point: geometric median of the kept shots
        "mean_x_mm": mm(mx), "mean_y_mm": mm(my),
        "move_x_mm": mm(-cx), "move_y_mm": mm(-cy),                 # how far the impact must move (+x right, +y up)
    })
    return out
