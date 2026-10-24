"""Feeds real server output through the dashboard's page logic in Node (skipped if Node is absent)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from fetserver.core import Core
from fetserver.db import Database

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 1_000_000_000
ALICE, BOB = "alice-client-01", "bob-client-0002"
SHOT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":%s , "N":1, "E":2, "S":3, "W":0 , '
        '"V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }')
MISS = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class DashboardLogic(unittest.TestCase):
    def test_page_logic_against_real_api_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [BASE]
            core = Core(Database(os.path.join(tmp, "t.db")), now_ms=lambda: now[0], lease_ms=10**9)
            hb = lambda did, name, **kw: core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": now[0] - BASE, "name": name, **kw})
            seq = [0]

            def shoot(did, x="0.00", y="0.00", raw=None):
                seq[0] += 1
                now[0] += 1000
                core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": now[0] - BASE, "events": [
                    {"seq": seq[0], "type": "shot", "uptime_ms": now[0] - BASE, "raw": raw or SHOT % (seq[0], x, y)}]})

            hb("lane-1", "Lane 1")
            hb("lane-2", "<img src=x onerror=alert(1)>", queue_depth=3)
            hb("lane-3", "Lane 3")
            hb("lane-4", "Lane 4")
            # lane-1: a match. Sighters, then 13 record shots with inner tens and a miss; an older closed series too.
            core.acquire_control("lane-1", ALICE, "Alice")
            core.set_mode("lane-1", ALICE, "record")
            for i in range(25):
                shoot("lane-1", "1.00", "1.00")
            core.set_mode("lane-1", ALICE, "sighter", new_series=True)
            for i in range(3):
                shoot("lane-1", "6.00", "0.00")
            core.set_mode("lane-1", ALICE, "record")
            for x in ("0.00", "2.00", "2.60", "3.10", "1.00", "4.20", "0.50", "2.80", "0.30", "6.00", "0.00"):
                shoot("lane-1", x, "0.00")
            shoot("lane-1", raw=MISS % 99)
            shoot("lane-1", "2.00", "0.00")
            # lane-2: practice, hostile names, 12 shots
            core.acquire_control("lane-2", BOB, "Bob <script>alert(1)</script>")
            core.set_mode("lane-2", BOB, "practice", shooter="<b>Mallory</b>")
            for i in range(12):
                shoot("lane-2", "2.50", "-1.00")
            # lane-4: sighter with a few shots, a hostile shooter name
            core.acquire_control("lane-4", ALICE, "Alice")
            core.set_mode("lane-4", ALICE, "sighter", shooter="<u>Eve</u>")
            for x in ("1.00", "9.00", "-3.00"):
                shoot("lane-4", x, "0.00")
            # lane-5: an ammo test with a 5.7 mm projectile, 14 shots, one miss, hostile label
            hb("lane-5", "Lane 5")
            core.acquire_control("lane-5", ALICE, "Alice")
            core.set_settings("lane-5", ALICE, projectile_mm=5.7)
            core.set_mode("lane-5", ALICE, "ammo", label="<i>Lot 7</i>")
            for dx, dy in ((0, 0), (1.1, 0.4), (-0.8, 1.2), (0.3, -1.5), (2.0, 0.9), (-1.4, -0.6), (0.7, 1.9), (-0.2, 0.1),
                           (1.6, -1.1), (-1.9, 0.8), (0.4, 0.4), (1.0, 1.0), (-0.5, -1.0)):
                shoot("lane-5", "%.2f" % (12.0 + dx), "%.2f" % (-8.0 + dy))
            shoot("lane-5", raw=MISS % 98)
            now[0] += 60_000                                    # lane-3 never reported again: offline
            for did, name in (("lane-1", "Lane 1"), ("lane-2", "<img src=x onerror=alert(1)>"), ("lane-4", "Lane 4"), ("lane-5", "Lane 5")):
                hb(did, name, **({"queue_depth": 3} if did == "lane-2" else {}))
            targets = core.list_targets(ALICE)
            series = core.list_series("lane-1")
            closed = next(s["id"] for s in series if not s["open"])
            payload = {"targets": targets, "series": series, "review": core.series_view(closed, "scored", None, 15)}
        proc = subprocess.run(["node", os.path.join(HERE, "dashboard_check.js")], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
