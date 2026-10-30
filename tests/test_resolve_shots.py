"""tools/resolve_shots.py: re-solving stored upstream shots from a FeT Monitor database (read-only)."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fetserver.core import Core
from fetserver.db import Database
from fetserver.shotsolve import Geometry, forward, upstream_solve
from fetserver.simtarget import ECHO_SAMPLE, shot_line

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("resolve_shots", ROOT / "tools/resolve_shots.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
GEOM = Geometry.from_upstream({"SENSOR": 230, "ANGLE": 45})


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "t.db")
        self.core = Core(Database(self.path))
        self.seq = 0

    def send(self, raw, etype="shot"):
        self.seq += 1
        r = self.core.handle_events({"device_id": "t1", "boot_count": 1, "uptime_ms": 5000 + self.seq,
                                     "events": [{"seq": self.seq, "type": etype, "uptime_ms": 4000 + self.seq, "raw": raw}]})
        self.assertEqual(len(r["acked"]), 1, r)

    def shot_at(self, x, y, temp=20.0, bump=None):
        """What upstream firmware sends for a shot at (x, y) (sensor frame), optionally with one counter off."""
        t = forward(x, y, GEOM, temp)
        if bump:
            t[bump[0]] += bump[1]
        r = upstream_solve(t, GEOM, temp)
        self.send(shot_line(self.seq + 1, r["x"], r["y"], r["counts"], 1.0, temp=temp))

    def test_consistent_shots_have_no_misfit(self):
        self.send(ECHO_SAMPLE, "config")
        for x, y in ((0.0, 0.0), (5.0, -8.0), (-14.0, 3.0)):
            self.shot_at(x, y)
        self.shot_at(2.0, 2.0, bump=(1, 40))                       # East 5 us late
        res = tool.analyse(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True))
        self.assertEqual(len(res), 4)
        for r in res[:3]:
            self.assertTrue(r["ok"])
            self.assertLess(r["misfit_mm"], 0.1)
            self.assertLess(r["moved_mm"], 0.1, "same position as the target reported")
        self.assertGreater(res[3]["misfit_mm"], 0.3, "least squares spreads one late microphone over all four")

    def test_settings_come_from_the_targets_report(self):
        self.send(ECHO_SAMPLE.replace('"SENSOR": 230.000000', '"SENSOR": 240.000000'), "config")
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.assertEqual(tool.settings_for(conn, "t1")["SENSOR"], 240.0)
        self.assertEqual(tool.settings_for(conn, "nobody"), {})

    def test_read_only(self):
        self.shot_at(1.0, 1.0)
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        tool.analyse(conn)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM shots")


if __name__ == "__main__":
    unittest.main()
