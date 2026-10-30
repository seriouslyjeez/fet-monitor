#!/usr/bin/env python3
"""Re-solve the shots stored in a FeT Monitor database with fetserver/shotsolve.py (read-only).

    python3 tools/resolve_shots.py path/to/fetserver.db [--device ID] [--since YYYY-MM-DD] [--csv out.csv]

For every non-miss shot from upstream freETarget firmware: the position the target reported, the position the new
solver gets from the same counters, the difference, and the misfit (how far the four microphone times are from
agreeing on one point, with the target's own geometry from its latest settings report). A large misfit across many
shots means the geometry is off (sensor positions, z offset, temperature); the accuracy test (aiming sheets) is how
to find the right values. Opens the database read-only; safe on a live copy.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import math
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetserver.lineparse import parse_echo                     # noqa: E402
from fetserver.shotsolve import Geometry, solve, timers_from_reported   # noqa: E402


def settings_for(conn, device_id: str) -> dict:
    row = conn.execute("SELECT raw FROM events WHERE device_id=? AND type='config' ORDER BY id DESC LIMIT 1",
                       (device_id,)).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return parse_echo(row[0])
    except Exception:
        return {}


def rows(conn, device=None, since_ms=None):
    q = ("SELECT device_id, event_ms, shot_number, x_h, y_h, n, e, s, w, temp_c FROM shots "
         "WHERE is_miss=0 AND n IS NOT NULL")
    args = []
    if device:
        q += " AND device_id=?"
        args.append(device)
    if since_ms:
        q += " AND event_ms>=?"
        args.append(since_ms)
    return conn.execute(q + " ORDER BY device_id, event_ms, id", args).fetchall()


def analyse(conn, device=None, since_ms=None):
    out, geoms = [], {}
    for dev, ms, no, xh, yh, n, e, s, w, temp in rows(conn, device, since_ms):
        if dev not in geoms:
            geoms[dev] = Geometry.from_upstream(settings_for(conn, dev))
        g = geoms[dev]
        x, y = xh / 100.0, yh / 100.0
        timers = timers_from_reported([n, e, s, w], x, y, g)
        sol = solve(timers, g, temp if temp is not None else 20.0)
        out.append({"device": dev, "time": datetime.datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds"),
                    "shot": no, "x": x, "y": y, "new_x": round(sol.x, 3) if sol.ok else None,
                    "new_y": round(sol.y, 3) if sol.ok else None,
                    "moved_mm": round(math.hypot(sol.x - x, sol.y - y), 3) if sol.ok else None,
                    "misfit_us": round(sol.residual_us, 3) if sol.ok and sol.residual_us is not None else None,
                    "misfit_mm": round(sol.residual_mm, 3) if sol.ok and sol.residual_mm is not None else None,
                    "signs": "".join("+" if r > 0 else "-" for r in sol.residuals) if sol.ok else "",
                    "ok": sol.ok, "reason": sol.reason})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db")
    ap.add_argument("--device")
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--csv")
    a = ap.parse_args(argv)
    since_ms = int(datetime.datetime.fromisoformat(a.since).timestamp() * 1000) if a.since else None
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    res = analyse(conn, a.device, since_ms)
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            wtr = csv.DictWriter(f, fieldnames=list(res[0]) if res else ["device"])
            wtr.writeheader()
            wtr.writerows(res)
    by = {}
    for r in res:
        by.setdefault(r["device"], []).append(r)
    for dev, rs in by.items():
        good = [r for r in rs if r["ok"]]
        print(f"{dev}: {len(rs)} shots, {len(rs) - len(good)} not solved")
        if good:
            mis = [r["misfit_mm"] for r in good if r["misfit_mm"] is not None]
            mv = [r["moved_mm"] for r in good]
            print(f"  misfit mm: median {statistics.median(mis):.2f}, 90% {sorted(mis)[int(len(mis) * .9)]:.2f}, max {max(mis):.2f}")
            print(f"  new vs reported position, mm: median {statistics.median(mv):.2f}, max {max(mv):.2f}")
            signs = {}
            for r in good:
                signs[r["signs"]] = signs.get(r["signs"], 0) + 1
            print("  misfit sign pattern N E S W (count):",
                  ", ".join(f"{k} {v}" for k, v in sorted(signs.items(), key=lambda kv: -kv[1])[:4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
