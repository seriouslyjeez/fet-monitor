#!/usr/bin/env python3
"""Read-only look inside the freETarget database: where did each shot go, and does its time make sense?
    sudo python3 diag.py /var/lib/freetarget/fetserver.db [device_id] [--n 15]
Opens the database read-only; it cannot change anything and is safe while the server runs."""
import argparse
import sqlite3
import sys
import time
from datetime import datetime


def ts(ms):
    return "-" if ms is None else datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S.%f")[:-3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("device", nargs="?")
    ap.add_argument("--n", type=int, default=15)
    a = ap.parse_args()
    c = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    c.row_factory = sqlite3.Row
    now = int(time.time() * 1000)
    print("Pi clock now:", datetime.fromtimestamp(now / 1000).strftime("%Y-%m-%d %H:%M:%S"))
    devs = [r[0] for r in c.execute("SELECT device_id FROM targets ORDER BY last_seen_ms DESC")]
    if not devs:
        sys.exit("no targets in the database")
    dev = a.device or devs[0]
    t = c.execute("SELECT * FROM targets WHERE device_id=?", (dev,)).fetchone()
    print("targets:", ", ".join(devs), "| looking at:", dev)
    print("last seen: %s  from IP %s  boot_count=%s  queue_depth=%s  overflow=%s  fw=%s" % (ts(t["last_seen_ms"]), t["last_ip"], t["boot_count"], t["queue_depth"], t["overflow_count"], t["bridge_fw"]))

    print("\nBOOTS (server's idea of when each ESP start-up happened)")
    for r in c.execute("SELECT * FROM boots WHERE device_id=? ORDER BY boot_count DESC LIMIT 6", (dev,)):
        print("  boot %-3s started %s" % (r["boot_count"], ts(r["boot_start_ms"])))

    print("\nMODE WINDOWS (segments)")
    for r in c.execute("SELECT * FROM segments WHERE device_id=? ORDER BY id DESC LIMIT 8", (dev,)):
        print("  seg %-3s %-8s series %-3s %s -> %s  shooter=%s" % (r["id"], r["mode"], r["series_id"], ts(r["started_ms"]), ts(r["ended_ms"]) if r["ended_ms"] else "(open)", r["shooter"]))

    print("\nLAST EVENTS (type, boot/seq, time the server assigned, time it arrived, difference)")
    rows = c.execute("SELECT * FROM events WHERE device_id=? ORDER BY id DESC LIMIT ?", (dev, a.n)).fetchall()
    for r in reversed(rows):
        print("  #%-4s %-12s %s/%-4s event=%s recv=%s  late=%6.1fs  quality=%s%s" % (
            r["id"], r["type"], r["boot_count"], r["seq"], ts(r["event_ms"]), ts(r["recv_ms"]),
            (r["recv_ms"] - r["event_ms"]) / 1000.0, r["time_quality"], "  PARSE ERROR: " + r["parse_error"] if r["parse_error"] else ""))

    print("\nLAST SHOTS (where each landed)")
    rows = c.execute("SELECT * FROM shots WHERE device_id=? ORDER BY id DESC LIMIT ?", (dev, a.n)).fetchall()
    for r in reversed(rows):
        print("  shot id %-4s #%-3s %s  ring=%s tenths=%s  series=%s stream=%s seg=%s n=%s%s" % (
            r["id"], r["shot_number"], ts(r["event_ms"]), r["ring"], r["tenths"], r["series_id"], r["stream"], r["segment_id"],
            r["series_no"], "   <-- NOT FILED IN ANY SERIES" if r["series_id"] is None else ""))

    n = c.execute("SELECT COUNT(*) FROM event_conflicts WHERE device_id=?", (dev,)).fetchone()[0]
    print("\nEVENT CONFLICTS (same boot+seq arrived twice with different content; kept aside): %d" % n)
    for r in c.execute("SELECT * FROM event_conflicts WHERE device_id=? ORDER BY id DESC LIMIT 5", (dev,)):
        print("  boot %s seq %s %s: %.70s" % (r["boot_count"], r["seq"], r["type"], r["raw"]))
    sh = c.execute("SELECT COUNT(*) FROM events WHERE device_id=? AND type='shot'", (dev,)).fetchone()[0]
    sr = c.execute("SELECT COUNT(*) FROM shots WHERE device_id=?", (dev,)).fetchone()[0]
    un = c.execute("SELECT COUNT(*) FROM shots WHERE device_id=? AND series_id IS NULL", (dev,)).fetchone()[0]
    print("\nTOTALS: shot events=%d  shot rows=%d  not filed in a series=%d" % (sh, sr, un))


if __name__ == "__main__":
    main()
