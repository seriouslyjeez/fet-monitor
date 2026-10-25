"""A device that loses its boot counter (flash erased) reuses boot numbers. Its shots must still be timed from the
current run, or they land in an old mode window and vanish from the screen."""
import os
import tempfile
import unittest

from fetserver.core import Core
from fetserver.db import Database

SHOT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":0.00, "y":0.00 , "N":1, "E":2, "S":3, "W":0 , '
        '"V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }')
BASE = 1_000_000_000
DEV = "fet-a1b2c3"
ME = "client-abcdefgh"


class BootReuse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = BASE
        self.core = Core(Database(os.path.join(self.tmp.name, "t.db")), now_ms=lambda: self.t, lease_ms=3_600_000)

    def hb(self, boot, uptime):
        self.core.handle_heartbeat({"device_id": DEV, "boot_count": boot, "uptime_ms": uptime})

    def shot(self, boot, seq, uptime, sent_uptime=None):
        """A shot seen at `uptime`, delivered in a request the device sent at `sent_uptime` (default: at once)."""
        self.core.handle_events({"device_id": DEV, "boot_count": boot, "uptime_ms": uptime if sent_uptime is None else sent_uptime,
                                 "events": [{"seq": seq, "type": "shot", "uptime_ms": uptime, "raw": SHOT % seq}]})

    def test_reused_boot_number_does_not_hide_new_shots(self):
        # run 1: boot 2 starts, only heartbeats
        self.hb(2, 1000)
        # 42 minutes later the device is erased and reflashed; it counts 1, 2 again
        self.t = BASE + 2_530_000
        self.hb(2, 2000)
        self.core.acquire_control(DEV, ME, "Jake")
        self.core.set_mode(DEV, ME, "sighter")
        self.t += 5000
        self.shot(2, 1, 7000)                         # fired after the mode change
        ev = self.core.db.conn().execute("SELECT event_ms, time_quality FROM events").fetchone()
        self.assertEqual(ev["time_quality"], "reanchored")
        self.assertAlmostEqual(ev["event_ms"], self.t - 0, delta=100)       # it happened "now", not 42 minutes ago
        self.assertEqual(len(self.core.list_shots(DEV)), 1)
        row = self.core.db.conn().execute("SELECT stream, series_id FROM shots").fetchone()
        self.assertEqual(row["stream"], "sighter")                           # filed under the mode in force

    def test_late_delivery_from_the_reused_boot_is_timed_from_the_new_run(self):
        self.hb(2, 1000)                                      # old run of "boot 2"
        self.t = BASE + 2_530_000
        self.hb(2, 2000)                                      # device wiped; "boot 2" again, restarted 42 minutes later
        self.core.acquire_control(DEV, ME, "Jake")
        self.core.set_mode(DEV, ME, "sighter")
        self.t += 5000
        self.shot(2, 1, 7000)                                 # re-anchors boot 2 to the new run
        # a shot is fired in this run but cannot be delivered; the device loses power and comes back as boot 7
        shot_uptime = 60_000                                  # 60 s into the run
        fired_at = (BASE + 2_530_000 - 2000) + shot_uptime
        self.t = fired_at + 120_000                           # two minutes later, after a power cut
        self.hb(7, 1000)
        self.core.handle_events({"device_id": DEV, "boot_count": 7, "uptime_ms": 2000, "events": [
            {"seq": 9, "boot_count": 2, "type": "shot", "uptime_ms": shot_uptime, "raw": SHOT % 9}]})
        r = self.core.db.conn().execute("SELECT event_ms FROM events WHERE seq=9").fetchone()
        self.assertAlmostEqual(r["event_ms"], fired_at, delta=100)

    def test_honest_drift_is_not_reanchored(self):
        self.hb(1, 1000)
        self.t += 3_600_000 * 20                                              # 20 hours up, crystal drifted a few seconds
        self.t += 8000
        self.shot(1, 1, 3_600_000 * 20 + 1000)
        q = self.core.db.conn().execute("SELECT time_quality FROM events").fetchone()[0]
        self.assertEqual(q, "ok")

    def test_late_batch_from_the_same_boot_keeps_exact_time(self):
        self.hb(3, 1000)
        self.t += 600_000                                                     # a 10 minute outage
        self.shot(3, 1, 6000, sent_uptime=601_000)                            # fired 5 s after the first heartbeat, delivered 10 min later
        r = self.core.db.conn().execute("SELECT event_ms, time_quality FROM events").fetchone()
        self.assertEqual(r["time_quality"], "ok")
        self.assertEqual(r["event_ms"], BASE - 1000 + 6000)       # boot start + 6 s, whatever the delay


if __name__ == "__main__":
    unittest.main()
