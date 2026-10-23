"""Kill -9 the writer in the middle of ingest and check that nothing acknowledged is lost."""
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest

from fetserver.db import Database

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = r"""
import sys
from fetserver.db import Database
from fetserver.core import Core
core = Core(Database(sys.argv[1]))
line = '{"shot":1, "miss":0, "name":"T", "time":1.00 ,"x":1.00, "y":1.00 , "N":1, "E":2, "S":3, "W":0 , "V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }'
seq = 0
while True:
    events = []
    for _ in range(5):
        seq += 1
        events.append({"seq": seq, "type": "shot", "uptime_ms": seq, "raw": line})
    reply = core.handle_events({"device_id": "k1", "boot_count": 1, "uptime_ms": seq + 10, "events": events})
    for b, s in reply["acked"]:
        print(s, flush=True)          # printed only after the commit has returned
"""


class KillNine(unittest.TestCase):
    def test_acknowledged_events_survive_sigkill(self):
        for attempt in range(3):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "k.db")
                Database(path)
                proc = subprocess.Popen([sys.executable, "-c", CHILD, path], cwd=ROOT,
                                        stdout=subprocess.PIPE, text=True)
                acked = []
                watchdog = threading.Timer(20, proc.kill)      # never hang the suite if the child stalls
                watchdog.start()
                target = 40 + attempt * 37
                for line in proc.stdout:
                    acked.append(int(line))
                    if len(acked) >= target:
                        proc.send_signal(signal.SIGKILL)
                        break
                watchdog.cancel()
                rest = proc.stdout.read()           # anything printed before the kill landed
                acked += [int(x) for x in rest.split()]
                proc.wait()
                proc.stdout.close()
                self.assertGreaterEqual(len(acked), target, "writer stalled or never acknowledged anything")
                db = Database(path)
                self.assertEqual(db.integrity_check(), "ok")
                stored = {r[0] for r in db.conn().execute("SELECT seq FROM events")}
                missing = [s for s in acked if s not in stored]
                self.assertEqual(missing, [], f"acknowledged but lost: {missing[:5]}")
                shots = db.conn().execute("SELECT COUNT(*) FROM shots").fetchone()[0]
                self.assertEqual(shots, len(stored))     # events and derived rows never disagree


if __name__ == "__main__":
    unittest.main()
