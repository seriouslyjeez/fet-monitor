"""Shot delivery time per target (Core.delivery, Range Control's "Shot delivery"): from the bridge to the server,
against the 5-second goal (docs/PLAN-deeptens.md)."""
import os
import tempfile
import unittest

from fetserver.core import Core
from fetserver.db import Database
from tests.test_matches import BASE, SHOT, Clock


class Delivery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.core = Core(Database(os.path.join(tmp.name, "t.db")), now_ms=self.clock)
        self.seq = 0

    def at(self, s):
        self.clock.t = BASE + int(s * 1000)

    def shot(self, did, got_s, sent_s, boot=1, of_boot=None):
        """A shot the bridge got at got_s (its uptime, boot started at BASE) that reached the server at sent_s;
        of_boot: the shot is from that earlier boot, sent in a request of this one."""
        self.seq += 1
        self.at(sent_s)
        ev = {"seq": self.seq, "type": "shot", "uptime_ms": int(got_s * 1000), "raw": SHOT % (self.seq, "1.00")}
        if of_boot is not None:
            ev["boot_count"] = of_boot
        self.core.handle_events({"device_id": did, "boot_count": boot, "uptime_ms": int(sent_s * 1000), "events": [ev]})

    def test_worst_median_and_over_the_goal(self):
        self.at(0)
        for did in ("lane-1", "lane-2"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})
        self.shot("lane-1", 10, 10.3)
        self.shot("lane-1", 20, 20.8)
        self.shot("lane-1", 30, 37.5)                          # after a WiFi drop: 7.5 s
        self.shot("lane-1", 45, 45.5)
        self.shot("lane-2", 40, 40.2)
        d = self.core.delivery()
        self.assertEqual(d["lane-1"], {"shots": 4, "worst_ms": 7500, "median_ms": 500, "over_goal": 1, "goal_ms": 5000},
                         "300, 500, 800, 7500: the lower of the middle two")
        self.assertEqual((d["lane-2"]["worst_ms"], d["lane-2"]["over_goal"]), (200, 0))
        t = next(x for x in self.core.admin_view()["targets"] if x["device_id"] == "lane-1")
        self.assertEqual(t["delivery"]["worst_ms"], 7500, "Range Control has it")

    def test_only_the_last_24_hours_and_known_times(self):
        self.at(0)
        self.core.handle_heartbeat({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 0})
        self.shot("lane-1", 10, 19)
        self.at(25 * 3600)
        self.shot("lane-1", 25 * 3600, 25 * 3600 + 0.4)
        self.assertEqual(self.core.delivery()["lane-1"]["shots"], 1, "the 9 s shot is a day old")
        self.shot("lane-1", 25 * 3600 - 5, 25 * 3600 + 1, of_boot=6)   # from a boot never heard: time unknown
        self.assertEqual(self.core.db.conn().execute("SELECT time_quality FROM events ORDER BY id DESC").fetchone()[0], "unknown")
        self.assertEqual(self.core.delivery()["lane-1"]["shots"], 1, "left out")


if __name__ == "__main__":
    unittest.main()
