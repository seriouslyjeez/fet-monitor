"""Sight adjustment: flyers, the aim point, the direction of the correction, and the practice-only API."""
import math
import os
import random
import tempfile
import unittest
from unittest import mock

from fetserver.core import BadRequest, Core, NotFound
from fetserver.db import Database
from fetserver import sightadj
from fetserver.sightadj import FLYER_MIN_H, find_flyers, geometric_median, suggest

from tests.test_http import ServerCase

ALICE = "alice-client-01"
SHOT = '{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":%s , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'
MISS = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'


def total_dist(c, pts):
    return sum(math.hypot(x - c[0], y - c[1]) for x, y in pts)


def shots(pts, misses=()):
    out = [{"n": i + 1, "x_h": x, "y_h": y, "miss": False} for i, (x, y) in enumerate(pts)]
    for n in misses:
        out.insert(n - 1, {"n": n, "x_h": None, "y_h": None, "miss": True})
    for i, s in enumerate(out):
        s["n"] = i + 1
    return out


class GeometricMedian(unittest.TestCase):
    def test_minimises_the_total_distance(self):
        rnd = random.Random(7)
        for trial in range(200):
            n = rnd.randint(1, 12)
            pts = [(rnd.randint(-800, 800), rnd.randint(-800, 800)) for _ in range(n)]
            if trial % 5 == 0 and n > 1:
                pts[1] = pts[0]                              # duplicates, a common case with tight groups
            c = geometric_median([(float(x), float(y)) for x, y in pts])
            best = total_dist(c, pts)
            for dx in (-1.0, 0.0, 1.0):                      # no neighbour 0.01 mm away is better (convex objective)
                for dy in (-1.0, 0.0, 1.0):
                    self.assertLessEqual(best, total_dist((c[0] + dx, c[1] + dy), pts) + 1e-6, (pts, c))
            mean = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
            self.assertLessEqual(best, total_dist(mean, pts) + 1e-6)

    def test_a_data_point_can_be_the_median(self):
        for pts in ([(0, 0), (100, 0), (300, 0)], [(0, 0), (0, 0), (0, 0), (500, 500)], [(0, 0), (-100, 0), (100, 0), (0, 100), (0, -100)]):
            c = geometric_median([(float(x), float(y)) for x, y in pts])
            want = (100, 0) if pts[2] == (300, 0) else (0, 0)
            self.assertAlmostEqual(c[0], want[0], places=2)
            self.assertAlmostEqual(c[1], want[1], places=2)


class Flyers(unittest.TestCase):
    TIGHT = [(0, 0), (60, 40), (-50, 30), (20, -60), (-30, -20), (40, 10)]

    def test_one_far_shot_is_a_flyer(self):
        self.assertEqual(find_flyers(self.TIGHT + [(900, -700)]), [6])

    def test_a_normal_group_has_none(self):
        self.assertEqual(find_flyers(self.TIGHT), [])
        rnd = random.Random(3)                               # round, normally spread groups (sigma 1.5 mm): seldom a flyer
        flagged = sum(1 for _ in range(500) if find_flyers([(int(rnd.gauss(0, 150)), int(rnd.gauss(0, 150))) for _ in range(10)]))
        self.assertLess(flagged, 500 * 0.18)
        caught = sum(1 for _ in range(500) if 10 in find_flyers([(int(rnd.gauss(0, 150)), int(rnd.gauss(0, 150))) for _ in range(10)] + [(900, 0)]))
        self.assertGreater(caught, 500 * 0.95, "a shot 6 sigma out is a flyer")

    def test_never_below_the_floor(self):
        tiny = [(0, 0), (5, 0), (0, 5), (-5, 0)]             # 0.05 mm group: a shot 1.4 mm away is not a flyer
        self.assertEqual(find_flyers(tiny + [(140, 0)]), [])
        self.assertEqual(find_flyers(tiny + [(FLYER_MIN_H + 10, 0)]), [4])

    def test_three_shots_always_remain(self):
        self.assertEqual(find_flyers([(0, 0), (10, 0), (2000, 0)]), [], "three shots: nothing can be dropped")
        pts = [(0, 0), (10, 0), (0, 10), (-10, 0), (5, 5), (1500, 0), (-1600, 0)]
        self.assertEqual(sorted(find_flyers(pts)), [5, 6])
        pts = [(0, 0), (10, 0), (0, 10), (1500, 0), (-1600, 0), (0, 1700)]
        self.assertEqual(find_flyers(pts), [], "half the shots far apart: no group to call them flyers against")
        fly = find_flyers([(0, 0), (10, 0), (0, 10), (-10, 0), (5, 5), (-1500, 0), (1900, 0)])
        self.assertEqual(fly, [6, 5], "farthest first")
        rnd = random.Random(5)
        for _ in range(2000):                                # whatever the spread, at least three shots are kept
            n = rnd.randint(1, 8)
            pts = [(rnd.choice((0, 3000)) + rnd.randint(-50, 50), rnd.randint(-50, 50)) for _ in range(n)]
            self.assertGreaterEqual(len(pts) - len(find_flyers(pts)), min(3, n))
        with mock.patch.object(sightadj, "FLYER_FACTOR", 0.5):    # the cap is a safety net: unreachable at 3.0, so force it
            pts = [(0, 0), (300, 0), (600, 0), (900, 0), (1300, 0)]
            fly = find_flyers(pts)
            self.assertEqual(len(fly), 2)
            self.assertEqual(fly, [4, 0], "farthest first")


class Suggest(unittest.TestCase):
    def test_direction_is_where_the_impact_must_move(self):
        d = suggest(shots([(200, -300), (210, -290), (190, -310), (205, -305)]))     # group right and low
        self.assertTrue(d["ready"])
        self.assertGreater(d["move_y_mm"], 2.9)     # up
        self.assertLess(d["move_x_mm"], -1.9)       # left
        self.assertAlmostEqual(d["move_x_mm"], -d["center_x_mm"])
        self.assertAlmostEqual(d["move_y_mm"], -d["center_y_mm"])

    def test_flyer_does_not_pull_the_aim_point(self):
        base = [(100, 100), (120, 90), (80, 110), (110, 120), (90, 80)]
        with_flyer = suggest(shots(base + [(-1500, -1500)]))
        without = suggest(shots(base))
        self.assertEqual(with_flyer["flyers"], [6])
        self.assertEqual(with_flyer["kept"], [1, 2, 3, 4, 5])
        self.assertAlmostEqual(with_flyer["center_x_mm"], without["center_x_mm"], places=2)
        self.assertAlmostEqual(with_flyer["center_y_mm"], without["center_y_mm"], places=2)
        self.assertEqual([p["flyer"] for p in with_flyer["points"]], [False] * 5 + [True])

    def test_aim_point_is_the_median_not_the_average(self):
        d = suggest(shots([(0, 0), (0, 0), (0, 0), (140, 0)]))     # 1.4 mm: inside the flyer floor, so it is kept
        self.assertEqual((d["flyers"], d["center_x_mm"], d["mean_x_mm"]), ([], 0.0, 0.35))

    def test_misses_are_counted_and_left_out(self):
        d = suggest(shots([(0, 0), (50, 0)], misses=(2,)))
        self.assertEqual((d["shots"], d["on_paper"], d["misses"], d["ready"]), (3, 2, 1, False))
        d = suggest(shots([(0, 0), (50, 0), (0, 50)], misses=(1,)))
        self.assertEqual((d["ready"], d["kept"]), (True, [2, 3, 4]))

    def test_needs_three_shots_on_paper(self):
        self.assertFalse(suggest(shots([(0, 0), (1, 1)]))["ready"])
        self.assertFalse(suggest([])["ready"])
        self.assertTrue(suggest(shots([(0, 0), (1, 1), (2, 2)]))["ready"])


class PracticeOnly(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = [1_000_000_000]
        self.core = Core(Database(os.path.join(tmp.name, "t.db")), now_ms=lambda: self.now[0], lease_ms=10**9)
        self.seq = 0
        self.core.handle_heartbeat({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 0, "name": "Lane 1"})
        self.core.acquire_control("lane-1", ALICE, "Alice")

    def shoot(self, x, y, raw=None):
        self.seq += 1
        self.now[0] += 1000
        self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": self.now[0] - 1_000_000_000, "events": [
            {"seq": self.seq, "type": "shot", "uptime_ms": self.now[0] - 1_000_000_000, "raw": raw or SHOT % (self.seq, x, y)}]})

    def live_series(self):
        return self.core.list_targets(ALICE)[0]["view"]["series_id"]

    def test_uses_shots_after_the_last_adjustment(self):
        self.core.set_mode("lane-1", ALICE, "practice", new_series=True)
        for x, y in (("3.00", "-2.00"), ("3.10", "-2.10"), ("2.90", "-1.90")):      # before the adjustment: right and low
            self.shoot(x, y)
        self.shoot(0, 0, raw=MISS % 99)
        for x, y in (("-1.00", "1.00"), ("-1.10", "0.90"), ("-0.90", "1.10")):      # after it: left and high
            self.shoot(x, y)
        sid = self.live_series()
        d = self.core.sight_suggestion(sid, 0)
        self.assertEqual((d["shots"], d["on_paper"], d["misses"]), (7, 6, 1))
        d = self.core.sight_suggestion(sid, 4)
        self.assertEqual((d["shots"], d["kept"], d["after"]), (3, [5, 6, 7], 4))
        self.assertAlmostEqual(d["move_x_mm"], 1.0, places=2)
        self.assertAlmostEqual(d["move_y_mm"], -1.0, places=2)
        self.assertFalse(self.core.sight_suggestion(sid, 5)["ready"])

    def test_refused_outside_practice(self):
        self.core.set_mode("lane-1", ALICE, "sighter", new_series=True)
        for _ in range(3):
            self.shoot("1.00", "1.00")
        with self.assertRaises(BadRequest):
            self.core.sight_suggestion(self.live_series())
        self.core.set_mode("lane-1", ALICE, "ammo", new_series=True)
        for _ in range(3):
            self.shoot("1.00", "1.00")
        with self.assertRaises(BadRequest):
            self.core.sight_suggestion(self.live_series())
        with self.assertRaises(NotFound):
            self.core.sight_suggestion(999999)


class SightHttp(ServerCase):
    def test_endpoint(self):
        self.core.handle_heartbeat({"device_id": "p1", "boot_count": 1, "uptime_ms": 0, "name": "P1"})
        self.core.acquire_control("p1", ALICE, "Alice")
        self.core.set_mode("p1", ALICE, "practice", new_series=True)
        for i, (x, y) in enumerate((("1.00", "0.00"), ("1.20", "0.10"), ("0.90", "-0.10")), 1):
            self.core.handle_events({"device_id": "p1", "boot_count": 1, "uptime_ms": i * 1000,
                                     "events": [{"seq": i, "type": "shot", "uptime_ms": i * 1000, "raw": SHOT % (i, x, y)}]})
        sid = self.core.list_targets(ALICE)[0]["view"]["series_id"]
        st, d = self.call("GET", f"/api/v1/series/{sid}/sight?after=0", token=None)
        self.assertEqual((st, d["ready"], d["kept"]), (200, True, [1, 2, 3]))
        self.assertLess(d["move_x_mm"], 0)
        self.assertEqual(self.call("GET", f"/api/v1/series/{sid}/sight?after=x", token=None)[0], 400)
        self.assertEqual(self.call("GET", "/api/v1/series/999999/sight", token=None)[0], 404)
        self.assertEqual(self.call("GET", f"/api/v1/series/{sid}/sights", token=None)[0], 404)


if __name__ == "__main__":
    unittest.main()
