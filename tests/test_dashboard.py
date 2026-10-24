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
SHOT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":%s , "N":1, "E":2, "S":3, "W":0 , '
        '"V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }')
MISS = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class DashboardLogic(unittest.TestCase):
    def test_page_logic_against_real_api_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [BASE]
            core = Core(Database(os.path.join(tmp, "t.db")), now_ms=lambda: now[0])
            hb = lambda did, name, **kw: core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": now[0] - BASE,
                                                                 "name": name, "calibre_x10": 45, **kw})
            hb("lane-1", "Lane 1")
            hb("lane-2", "<img src=x onerror=alert(1)>", queue_depth=3)
            hb("lane-3", "Lane 3")
            seq = [0]

            def shoot(did, raw):
                seq[0] += 1
                core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": now[0] - BASE,
                                    "events": [{"seq": seq[0], "type": "shot", "uptime_ms": now[0] - BASE, "raw": raw}]})

            core.acquire_control("lane-1", "alice-client-01", "Alice")
            now[0] += 1000
            core.set_mode("lane-1", "alice-client-01", "sighter")
            now[0] += 1000
            shoot("lane-1", SHOT % (1, "-3.61", "-14.56"))
            shoot("lane-1", MISS % 2)
            now[0] += 1000
            core.set_mode("lane-1", "alice-client-01", "sighter", shooter="<u>Eve</u>", new_segment=True)
            now[0] += 1000
            core.set_mode("lane-1", "alice-client-01", "record", shooter="Alice")
            now[0] += 1000
            shoot("lane-1", SHOT % (3, "0.00", "0.00"))
            core.acquire_control("lane-2", "bob-client-0002", "Bob <script>alert(1)</script>")
            core.set_mode("lane-2", "bob-client-0002", "record", shooter="<b>Mallory</b>")
            now[0] += 60_000                                   # lane-3 goes quiet; the others keep reporting
            hb("lane-1", "Lane 1")
            hb("lane-2", "<img src=x onerror=alert(1)>", queue_depth=3)
            core.acquire_control("lane-2", "bob-client-0002", "Bob <script>alert(1)</script>")
            targets = core.list_targets("alice-client-01")
            segments = core.list_segments("lane-1")
            current = next(t for t in targets if t["device_id"] == "lane-1")["segment_id"]
            payload = {"targets": targets, "segments": segments, "shots": core.list_shots("lane-1", segment_id=current)}
        proc = subprocess.run(["node", os.path.join(HERE, "dashboard_check.js")], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
