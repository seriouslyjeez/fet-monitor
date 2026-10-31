#!/usr/bin/env python3
"""Compare the target's shot positions with the holes measured on the frame cards (tools/accuracy/measure.py).

    tools/accuracy/report.py DB HOLES.json --device fet-704131 --series 140 --cards 55-60 61-66 68-73

DB: a copy of the server's database (opened read-only). --cards: the shot numbers on each card, in the order of the
photos in HOLES.json (a bounce or a shot without a photo: leave it out, e.g. 61-64,66). Each card's placement in the
frame is unknown, so its offset and rotation are fitted; what is left over is the target's own error.
Geometry: SENSOR / ANGLE / Z_OFFSET as set on the target (--sensor, --angle).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from fetserver.shotsolve import Geometry, solve, timers_from_reported   # noqa: E402


def shot_list(spec: str):
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def fit(A, B, rotate=True):
    """Best placement of A onto B (rotation about the centroid + offset): mapped A, rotation in degrees."""
    ca, cb = A.mean(0), B.mean(0)
    a0, b0 = A - ca, B - cb
    th = 0.0
    if rotate and len(A) > 1:
        H = a0.T @ b0
        th = math.atan2(H[0, 1] - H[1, 0], H[0, 0] + H[1, 1])
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    return a0 @ R.T + cb, math.degrees(th)


def evaluate(label, pos, truth, cards, rotate=True, verbose=False, out=sys.stdout):
    errs = []
    for k, card in enumerate(cards):
        M, th = fit(np.array([pos[n] for n in card]), np.array([truth[n] for n in card]), rotate)
        e = np.linalg.norm(M - np.array([truth[n] for n in card]), axis=1)
        errs += e.tolist()
        if verbose:
            print(f"   card {k + 1}: rotation {th:6.1f} deg", file=out)
            for n, m, ee in zip(card, M, e):
                b = truth[n]
                print(f"     #{n}: target ({m[0]:6.2f},{m[1]:6.2f})  hole ({b[0]:6.2f},{b[1]:6.2f})  error {ee:5.2f} mm",
                      file=out)
    e = np.array(errs)
    print(f"{label:50s} median {np.median(e):.2f}  mean {e.mean():.2f}  max {e.max():.2f} mm  ({len(e)} shots)", file=out)
    return e


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db")
    ap.add_argument("holes")
    ap.add_argument("--device", required=True)
    ap.add_argument("--series", type=int, required=True)
    ap.add_argument("--cards", nargs="+", required=True, help="shot numbers per card, e.g. 55-60 61-66")
    ap.add_argument("--sensor", type=float, default=230.0)
    ap.add_argument("--angle", type=float, default=45.0)
    a = ap.parse_args(argv)
    cards = [shot_list(c) for c in a.cards]
    order = [n for c in cards for n in c]
    holes = json.loads(Path(a.holes).read_text())
    if len(holes) != len(order):
        raise SystemExit(f"{len(holes)} photos in {a.holes} but {len(order)} shots in --cards")
    missing = [h["photo"] for h in holes if "hole" not in h]
    if missing:
        raise SystemExit("no hole found in " + ", ".join(missing) + ": look at its _rect.png")
    truth = {n: np.array(h["hole"]) for n, h in zip(order, holes)}
    c = sqlite3.connect(f"file:{a.db}?mode=ro&immutable=1", uri=True)
    rows = {r[0]: r for r in c.execute(
        "select shot_number, x_h, y_h, n, e, s, w, temp_c, is_miss from shots where device_id=? and series_id=?",
        (a.device, a.series))}
    absent = [n for n in order if n not in rows or rows[n][8]]
    if absent:
        raise SystemExit("not shots (or misses) in that series: " + ", ".join(map(str, absent)))
    g = Geometry.from_upstream({"SENSOR": a.sensor, "ANGLE": a.angle})
    up = {n: np.array((rows[n][1] / 100, rows[n][2] / 100)) for n in order}
    print("Each card's placement (offset, rotation) fitted; what is left is the target's error.")
    evaluate("target's own positions", up, truth, cards, verbose=True)
    evaluate("target's own positions, offset only", up, truth, cards, rotate=False)
    new, mis = {}, {}
    for n in order:
        r = rows[n]
        sol = solve(timers_from_reported(list(r[3:7]), r[1] / 100, r[2] / 100, g), g, r[7])
        new[n], mis[n] = np.array((sol.x, sol.y)), sol
    evaluate("fet-monitor solver (shotsolve)", new, truth, cards)
    print("\nmisfit (do the four microphone times agree on one point?):")
    for n in order:
        r, s = rows[n], mis[n]
        print(f"  #{n}: N{r[3]:6d} E{r[4]:6d} S{r[5]:6d} W{r[6]:6d}  misfit {s.residual_mm:5.2f} mm  " +
              "".join("+" if x > 0 else "-" for x in s.residuals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
