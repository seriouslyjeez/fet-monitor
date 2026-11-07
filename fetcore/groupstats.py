"""Group statistics for ammo testing, from shot centres in integer hundredths of a millimetre.

The reported figures:
* extreme spread, centre to centre: the largest distance between any two shot centres (exact: the
  largest integer squared distance, square-rooted once);
* group size: that spread plus one projectile diameter, i.e. outside edge to outside edge;
* the oval: the smallest-area ellipse that contains every shot centre (Khachiyan's algorithm on the
  convex hull, then scaled so that containment is guaranteed, not merely approximate; the iteration
  stops when it is within about 0.05 % of the true minimum, far below the 0.01 mm shot resolution), with its
  major/minor axes and angle; the "outer" axes add one projectile diameter, which is the oval a
  pellet-sized pen would draw around the group;
* group centre = mean point of impact (the average of the shot centres);
* mean radius: average distance of the shots from the group centre.
Angles are in degrees from the +x axis (y up), 0 <= angle < 180.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Pt = Tuple[int, int]


def convex_hull(points: Sequence[Pt]) -> List[Pt]:
    """Andrew's monotone chain, exact integer arithmetic; collinear points are dropped."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Pt] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Pt] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inv3(m):
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def min_enclosing_ellipse(pts: Sequence[Tuple[float, float]], tol: float = 1e-8, max_iter: int = 2000):
    """Smallest-area ellipse containing pts (>= 3 points, not all collinear).

    Returns (cx, cy, a, b, angle_rad): semi-major a >= semi-minor b."""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    q = [(p[0] - mx, p[1] - my) for p in pts]
    scale = max(max(abs(x), abs(y)) for x, y in q) or 1.0
    q = [(x / scale, y / scale) for x, y in q]
    u = [1.0 / n] * n
    for _ in range(max_iter):
        v = [[0.0] * 3 for _ in range(3)]
        for ui, (x, y) in zip(u, q):
            w = (x, y, 1.0)
            for r in range(3):
                for c in range(3):
                    v[r][c] += ui * w[r] * w[c]
        vi = _inv3(v)
        best, bj = -1.0, 0
        for j, (x, y) in enumerate(q):
            w = (x, y, 1.0)
            m = sum(w[r] * vi[r][c] * w[c] for r in range(3) for c in range(3))
            if m > best:
                best, bj = m, j
        step = (best - 3.0) / (3.0 * (best - 1.0))
        if step <= tol:
            break
        u = [(1.0 - step) * ui for ui in u]
        u[bj] += step
    cx = sum(ui * x for ui, (x, _) in zip(u, q))
    cy = sum(ui * y for ui, (_, y) in zip(u, q))
    sxx = sum(ui * x * x for ui, (x, _) in zip(u, q)) - cx * cx
    syy = sum(ui * y * y for ui, (_, y) in zip(u, q)) - cy * cy
    sxy = sum(ui * x * y for ui, (x, y) in zip(u, q)) - cx * cy
    det = sxx * syy - sxy * sxy
    # A = S^-1 / 2 ; (p-c)^T A (p-c) <= 1
    axx, ayy, axy = syy / det / 2.0, sxx / det / 2.0, -sxy / det / 2.0
    worst = max(axx * (x - cx) ** 2 + 2 * axy * (x - cx) * (y - cy) + ayy * (y - cy) ** 2 for x, y in q)
    axx, ayy, axy = axx / worst, ayy / worst, axy / worst            # now every point is inside, one on the edge
    tr, dt = axx + ayy, axx * ayy - axy * axy
    disc = math.sqrt(max(0.0, tr * tr / 4 - dt))
    lam_small, lam_big = tr / 2 - disc, tr / 2 + disc               # small eigenvalue = long axis
    a, b = 1.0 / math.sqrt(lam_small), 1.0 / math.sqrt(lam_big)
    angle = 0.5 * math.atan2(2 * axy, axx - ayy) + math.pi / 2          # long axis is perpendicular to the big-eigenvalue direction
    return mx + cx * scale, my + cy * scale, a * scale, b * scale, angle


def group_stats(points: Sequence[Pt], dia_h: int) -> dict:
    """points: shot centres (x_h, y_h) in hundredths of a mm, relative to the first shot."""
    r_h = dia_h / 2.0
    out = {"shots": len(points), "diameter_mm": dia_h / 100.0}
    if not points:
        return out
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    hull = convex_hull(points)
    d2 = 0
    pair = (hull[0], hull[0])
    for i in range(len(hull)):
        for j in range(i + 1, len(hull)):
            dd = (hull[i][0] - hull[j][0]) ** 2 + (hull[i][1] - hull[j][1]) ** 2
            if dd > d2:
                d2, pair = dd, (hull[i], hull[j])
    es = math.sqrt(d2)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if len(hull) >= 3:
        ex, ey, a, b, ang = min_enclosing_ellipse([(float(x), float(y)) for x, y in hull])
    else:                                                    # one shot, two shots or all in a line: no area
        (x1, y1), (x2, y2) = pair
        ex, ey, a, b = (x1 + x2) / 2.0, (y1 + y2) / 2.0, es / 2.0, 0.0
        ang = math.atan2(y2 - y1, x2 - x1) if es else 0.0
    ang = math.degrees(ang) % 180.0
    mm = lambda h: round(h / 100.0, 2)
    out.update({
        "center_x_mm": mm(cx), "center_y_mm": mm(cy),
        "spread_cc_mm": mm(es), "size_mm": mm(es + dia_h),
        "width_mm": mm(max(xs) - min(xs) + dia_h), "height_mm": mm(max(ys) - min(ys) + dia_h),
        "mean_radius_mm": mm(sum(math.hypot(x - cx, y - cy) for x, y in points) / n),
        "ellipse": {"cx_mm": mm(ex), "cy_mm": mm(ey), "major_mm": mm(2 * a), "minor_mm": mm(2 * b),
                    "angle_deg": round(ang, 1),
                    "outer_major_mm": mm(2 * a + dia_h), "outer_minor_mm": mm(2 * b + dia_h)},
    })
    return out
