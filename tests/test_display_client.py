"""FeT Display's client (firmware/display/lib/client.py, http.py) under MicroPython against a real FeT Monitor on this
computer: ask for a code, wait while Range Control has not typed it, then the view; a shot answers the long poll; a
button; removed: "none". Skipped without `micropython`."""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server

ROOT = Path(__file__).resolve().parent.parent
MP = shutil.which("micropython")
SCRIPT = r"""
import sys, time, asyncio
sys.path.insert(0, %r)
import client

async def main():
    c = client.Client("127.0.0.1:%d", "cyd-test01", "0.1.0")
    code = await c.pair()
    print("CODE", code)
    while True:
        try:
            v = await c.view()
            break
        except client.NotAssigned as e:
            assert e.pairing == "pending", e.pairing
            await asyncio.sleep_ms(200)
    print("TARGET", v["target"]["device_id"], v["count"])
    ticks = [0]
    async def other():                                     # something else keeps running during the long poll
        while True:
            ticks[0] += 1
            await asyncio.sleep_ms(50)
    asyncio.create_task(other())
    t0 = time.ticks_ms()
    v2 = await c.view(v["rev"], 20)
    print("SHOT", v2["count"], v2["last"]["score"], time.ticks_diff(time.ticks_ms(), t0) < 5000, ticks[0] > 3)
    try:
        await c.act("start_record")
    except ValueError as e:
        print("REFUSED", e)
    try:
        await c.view(v2["rev"], 20)
    except client.NotAssigned as e:
        print("REMOVED", e.pairing)
    try:
        await client.Client("127.0.0.1:1", "x", "0").view()
    except OSError:
        print("UNREACHABLE")

asyncio.run(main())
"""


@unittest.skipUnless(MP, "micropython not installed (brew install micropython)")
class ClientUnderMicroPython(unittest.TestCase):
    def test_pair_view_shot_button_removed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = Core(Database(os.path.join(tmp.name, "t.db")))
        core.handle_heartbeat({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 0})
        srv = make_server(core, "tok", "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        script = os.path.join(tmp.name, "c.py")
        Path(script).write_text(SCRIPT % (str(ROOT / "firmware/display/lib"), srv.server_address[1]))
        proc = subprocess.Popen([MP, script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = proc.stdout.readline()
        self.assertTrue(line.startswith("CODE "), line)
        time.sleep(0.5)                                          # it waits: "pending"
        core.lane_displays.approve(line.split()[1], "lane-1")
        out = proc.stdout.readline()
        self.assertEqual(out.split(), ["TARGET", "lane-1", "0"])
        time.sleep(0.5)                                          # it waits on the long poll
        core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 1, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": 1, "raw": '{"shot":1, "miss":0, "x":1.00, "y":0.50, "N":1, "E":2, "S":3, "W":0}'}]})
        out = proc.stdout.readline().split()
        self.assertEqual((out[0], out[1], out[3], out[4]), ("SHOT", "1", "True", "True"), "at once, and the rest kept running")
        self.assertEqual(proc.stdout.readline().strip(), "REFUSED this target is not in a match")
        time.sleep(0.5)
        core.lane_displays.remove(core.lane_displays.list()["displays"][0]["id"])
        self.assertEqual(proc.stdout.readline().strip(), "REMOVED none")
        self.assertEqual(proc.stdout.readline().strip(), "UNREACHABLE")
        proc.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
