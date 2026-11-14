"""Compare two fetcore versions on every shot stored in FeT Monitor databases (read-only).

For each stored shot event it computes, with each version: the position in hundredths of a millimetre (a Mega line:
the x/y it printed; a FeT target line: the solver's position), the misfit in micrometres, and the score on every
face. Any difference is listed. Use it before releasing a fetcore change that can move a position (2026-11-13: the
same-bits change for the CYD display, docs/PLAN-cyd.md).

    git archive <old tag> fetcore | tar -x -C /tmp/old          # the old version, e.g. the release the Pi runs
    python3 tools/fetcore_compare.py --old /tmp/old fet-pi.db [more.db ...]

The databases are opened read-only: a copy of the Pi's /var/lib/fet-monitor/fetserver.db (its backup service
writes consistent ones to /var/backups/fet-monitor/), or a CSV from deepTENS with the header id,device_id,type,raw,
board_rev (devices_deviceevent joined to devices_device; psql's \copy ... WITH CSV HEADER).
"""
import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WORKER = r'''
import json, sys, dataclasses
from fetcore import faces as F
from fetcore.lineparse import parse_shot_line
from fetcore.shotsolve import Geometry, solve, timers_from_reported
from fetcore.targetproto import parse_shot

def at_rate(g, rate_hz):
    if rate_hz is None:
        return g
    if hasattr(g, "at_rate"):
        return g.at_rate(rate_hz)
    mhz = rate_hz / 1e6
    return dataclasses.replace(g, clock_mhz=mhz, walk_ticks=g.walk_ticks * mhz / g.clock_mhz)

def geometry(t):
    if t.get("geometry_json"):
        g = json.loads(t["geometry_json"])
        return Geometry(sensors=[tuple(p) for p in g["sensors"]], z_mm=g["z_mm"], angle_deg=g["angle_deg"],
                        clock_mhz=g["clock_mhz"], walk_ticks=g["walk_ticks"])
    if t.get("board_rev") is not None and t["board_rev"] >= 500:
        return Geometry.from_upstream({"SENSOR": 232, "ANGLE": 45}, clock_mhz=10.0)
    return Geometry.from_upstream({"SENSOR": 230, "ANGLE": 45})

def scores(x_h, y_h):
    out = {}
    for fid, f in sorted(F.FACES.items()):
        s = F.score(f, x_h, y_h)
        out[fid] = [s.ring, s.tenths, bool(s.inner)]
    return out

for line in sys.stdin:
    ev = json.loads(line)
    try:
        if ev["type"] == "shot":
            sl = parse_shot_line(ev["raw"])
            r = {"x_h": sl.x_h, "y_h": sl.y_h, "miss": sl.miss, "unplaced": sl.unplaced, "misfit_um": None}
            if not sl.miss and not sl.unplaced and None not in (sl.n, sl.e, sl.s, sl.w):
                g = geometry(ev["target"])
                sol = solve(timers_from_reported([sl.n, sl.e, sl.s, sl.w], sl.x_h / 100.0, sl.y_h / 100.0, g), g,
                            20.0 if sl.temp_c is None else sl.temp_c)
                r["misfit_um"] = int(round(sol.residual_mm * 1000)) if sol.ok and sol.residual_mm is not None else None
        else:
            p = parse_shot(ev["raw"], None)
            sol = solve(p["counts"], at_rate(geometry(ev["target"]), p["rate_hz"]), 20.0 if p["temp_c"] is None else p["temp_c"])
            r = {"ok": sol.ok, "x_h": int(round(sol.x * 100)) if sol.ok else None, "y_h": int(round(sol.y * 100)) if sol.ok else None,
                 "misfit_um": None if not sol.ok or sol.residual_mm is None else int(round(sol.residual_mm * 1000))}
        if r.get("x_h") is not None:
            r["scores"] = scores(r["x_h"], r["y_h"])
    except Exception as exc:
        r = {"error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(r, sort_keys=True))
'''


def csv_events(path):
    """deepTENS's export: id,device_id,type,raw,board_rev (a trailing "COPY n" line from psql is ignored)."""
    csv.field_size_limit(1 << 20)
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] not in ("shot", "target_shot") or not r["raw"] or (r["id"] or "").startswith("COPY"):
                continue
            rev = r.get("board_rev")
            yield {"id": int(r["id"]), "type": r["type"], "raw": r["raw"],
                   "target": {"device_id": r["device_id"], "board_rev": int(rev) if rev not in (None, "") else None}}


def events(path):
    if path.endswith(".csv"):
        yield from csv_events(path)
        return
    db = sqlite3.connect("file:%s?immutable=1" % path, uri=True)    # a copy: read as it is (a WAL copy cannot open mode=ro)
    db.row_factory = sqlite3.Row
    tcols = {r[1] for r in db.execute("PRAGMA table_info(targets)")}
    want = [c for c in ("device_id", "geometry_json", "board_rev") if c in tcols]
    targets = {r["device_id"]: dict(r) for r in db.execute("SELECT %s FROM targets" % ", ".join(want))}
    for r in db.execute("SELECT id, device_id, type, raw FROM events WHERE type IN ('shot', 'target_shot') AND raw IS NOT NULL ORDER BY id"):
        yield {"id": r["id"], "type": r["type"], "raw": r["raw"], "target": targets.get(r["device_id"], {})}


def run(fetcore_parent, evs):
    env = dict(os.environ, PYTHONPATH=fetcore_parent, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", WORKER], input="".join(json.dumps(e) + "\n" for e in evs),
                         capture_output=True, text=True, env=env, cwd=fetcore_parent, check=True).stdout
    return [json.loads(x) for x in out.splitlines()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="a folder holding the old fetcore/ package")
    ap.add_argument("--new", default=ROOT, help="a folder holding the new fetcore/ package (default: this checkout)")
    ap.add_argument("db", nargs="+")
    a = ap.parse_args(argv)
    total = differ = 0
    for path in a.db:
        evs = list(events(path))
        old, new = run(os.path.abspath(a.old), evs), run(os.path.abspath(a.new), evs)
        kinds = {}
        for ev, o, n in zip(evs, old, new):
            kinds[ev["type"]] = kinds.get(ev["type"], 0) + 1
            if o != n:
                differ += 1
                if differ <= 20:
                    print("DIFF %s event %d (%s): old %s new %s" % (os.path.basename(path), ev["id"], ev["type"], o, n))
        total += len(evs)
        errors = sum(1 for n in new if "error" in n)
        print("%s: %s shot events, %d errors (both versions alike)" % (os.path.basename(path), kinds, errors))
    print("%d shot events, %d differ" % (total, differ))
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
