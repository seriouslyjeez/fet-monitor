import math
import os
import tempfile
import unittest

from fetserver.core import BadRequest, Core, even_h
from fetserver.db import Database
from fetserver.groupstats import convex_hull, group_stats, min_enclosing_ellipse
from fetserver.scoring import score_xy
from tests.test_series import ALICE, BASE, MISS, SHOT, SeriesCase


class GroupStats(unittest.TestCase):
    def test_empty_one_two(self):
        self.assertEqual(group_stats([], 450), {"shots": 0, "diameter_mm": 4.5})
        g = group_stats([(0, 0)], 450)
        self.assertEqual((g["spread_cc_mm"], g["size_mm"], g["mean_radius_mm"]), (0.0, 4.5, 0.0))
        self.assertEqual((g["ellipse"]["major_mm"], g["ellipse"]["minor_mm"], g["ellipse"]["outer_minor_mm"]), (0.0, 0.0, 4.5))
        g = group_stats([(0, 0), (300, 400)], 450)                   # 3-4-5 triangle: spread exactly 5.00 mm
        self.assertEqual((g["spread_cc_mm"], g["size_mm"]), (5.0, 9.5))
        self.assertEqual((g["center_x_mm"], g["center_y_mm"]), (1.5, 2.0))
        self.assertEqual((g["ellipse"]["major_mm"], g["ellipse"]["minor_mm"], g["ellipse"]["angle_deg"]), (5.0, 0.0, 53.1))
        self.assertEqual((g["width_mm"], g["height_mm"]), (7.5, 8.5))

    def test_square_is_a_circle_through_the_corners(self):
        g = group_stats([(0, 0), (1000, 0), (1000, 1000), (0, 1000)], 450)
        self.assertEqual((g["spread_cc_mm"], g["size_mm"]), (14.14, 18.64))
        e = g["ellipse"]
        self.assertEqual((e["cx_mm"], e["cy_mm"], e["major_mm"], e["minor_mm"]), (5.0, 5.0, 14.14, 14.14))
        self.assertEqual(g["mean_radius_mm"], 7.07)

    def test_rotated_rectangle_gives_axes_and_angle(self):
        a = math.radians(30)
        pts = [(round((x * math.cos(a) - y * math.sin(a)) * 100), round((x * math.sin(a) + y * math.cos(a)) * 100))
               for x, y in [(-10, -2), (10, -2), (10, 2), (-10, 2)]]
        e = group_stats(pts, 450)["ellipse"]
        self.assertAlmostEqual(e["major_mm"], 28.28, delta=0.02)       # the input points are rounded to 0.01 mm
        self.assertAlmostEqual(e["minor_mm"], 5.66, delta=0.02)
        self.assertAlmostEqual(e["angle_deg"], 30.0, delta=0.1)
        self.assertAlmostEqual(e["outer_major_mm"] - e["major_mm"], 4.5, delta=0.011)

    def test_collinear_points_have_no_area(self):
        e = group_stats([(0, 0), (200, 0), (100, 0), (500, 0)], 450)["ellipse"]
        self.assertEqual((e["major_mm"], e["minor_mm"], e["angle_deg"], e["cx_mm"]), (5.0, 0.0, 0.0, 2.5))
        self.assertEqual(convex_hull([(0, 0), (1, 1), (2, 2)]), [(0, 0), (2, 2)])

    def test_every_shot_is_inside_the_oval_and_the_oval_is_minimal(self):
        import random
        rnd = random.Random(7)
        for _ in range(25):
            pts = [(rnd.randint(-900, 900), rnd.randint(-400, 400)) for _ in range(rnd.randint(3, 40))]
            hull = convex_hull(pts)
            if len(hull) < 3:
                continue
            cx, cy, a, b, th = min_enclosing_ellipse([(float(x), float(y)) for x, y in hull])
            on_edge = 0
            for x, y in pts:
                u = (x - cx) * math.cos(th) + (y - cy) * math.sin(th)
                v = -(x - cx) * math.sin(th) + (y - cy) * math.cos(th)
                q = (u / a) ** 2 + (v / b) ** 2
                self.assertLessEqual(q, 1 + 1e-9)
                on_edge += q > 1 - 1e-3
            self.assertGreaterEqual(on_edge, 2)                      # a minimal oval touches at least two shots

    def test_projectile_size_only_adds_to_the_outside_figures(self):
        pts = [(0, 0), (500, 0), (250, 300)]
        a, b = group_stats(pts, 450), group_stats(pts, 570)
        self.assertEqual(a["spread_cc_mm"], b["spread_cc_mm"])
        self.assertEqual(round(b["size_mm"] - a["size_mm"], 2), 1.2)


class AmmoSeries(SeriesCase):
    def ammo(self, **kw):
        self.control("ammo", **kw)

    def test_first_shot_is_always_10_9_and_the_rest_are_relative_to_it(self):
        self.ammo()
        self.shoot("7.00", "-20.00")                                  # far off the centre of the target
        self.shoot("7.50", "-20.00")                                  # 0.5 mm right of the first shot
        self.shoot("10.00", "-20.00")                                 # 3 mm right
        v = self.view()
        self.assertEqual(v["kind"], "ammo")
        shots = v["bull_shots"]
        self.assertEqual((shots[0]["decimal"], shots[0]["ring"], shots[0]["x_mm"], shots[0]["y_mm"]), (10.9, 10, 0.0, 0.0))
        self.assertEqual((shots[1]["x_mm"], shots[1]["y_mm"]), (0.5, 0.0))
        self.assertEqual(shots[1]["decimal"], score_xy(50, 0, 450).tenths / 10.0)
        self.assertEqual(shots[2]["decimal"], score_xy(300, 0, 450).tenths / 10.0)
        self.assertEqual(shots[2]["ring"], score_xy(300, 0, 450).ring)

    def test_no_limit_no_rollover_and_everything_stays_on_the_target(self):
        self.ammo()
        sid = self.core.list_targets()[0]["series_id"]
        for i in range(130):
            self.shoot("%.2f" % (i % 5 * 0.4), "%.2f" % (i % 7 * 0.3))
        v = self.view()
        self.assertEqual((v["count"], len(v["bull_shots"]), v["series_id"], v["total"]), (130, 130, sid, None))
        self.assertEqual(v["ammo"]["shots"], 130)
        self.assertEqual(self.core.list_targets()[0]["mode"], "ammo")

    def test_group_stats_are_live_and_use_the_projectile(self):
        self.ammo()
        for xy in [("1.00", "1.00"), ("4.00", "5.00"), ("1.00", "5.00")]:
            self.shoot(*xy)
        a = self.view()["ammo"]
        self.assertEqual((a["shots"], a["spread_cc_mm"], a["size_mm"], a["diameter_mm"]), (3, 5.0, 9.5, 4.5))
        self.assertEqual((a["center_x_mm"], a["center_y_mm"]), (1.0, 2.67))   # relative to the first shot: (0,0),(3,4),(0,4)
        self.assertEqual(self.view()["last_shot"]["n"], 3)

    def test_misses_are_zero_and_never_the_reference(self):
        self.ammo()
        self.shoot(raw=MISS % 1)
        self.shoot("5.00", "5.00")                                     # first mapped shot is the reference
        self.shoot("5.00", "6.00")
        v = self.view()
        self.assertEqual([(s["miss"], s["decimal"]) for s in v["bull_shots"]][:2], [(True, 0.0), (False, 10.9)])
        self.assertEqual((v["ammo"]["shots"], v["ammo"]["misses"], v["count"]), (2, 1, 3))    # shots = mapped shots
        self.assertEqual(v["ammo"]["spread_cc_mm"], 1.0)               # the miss has no position and is not measured

    def test_projectile_change_rescores_the_live_test(self):
        self.ammo()
        self.shoot("0.00", "0.00")
        self.shoot("2.30", "0.00")
        before = self.view()["bull_shots"][1]["decimal"]
        self.assertEqual(before, score_xy(230, 0, 450).tenths / 10.0)
        s = self.core.set_settings("a1", ALICE, projectile_mm=5.7)
        self.assertEqual(s["projectile_mm"], 5.7)
        v = self.view()
        self.assertEqual(v["bull_shots"][1]["decimal"], score_xy(230, 0, 570).tenths / 10.0)
        self.assertEqual(v["bull_shots"][0]["decimal"], 10.9)
        self.assertEqual(v["ammo"]["diameter_mm"], 5.7)
        self.assertEqual(v["ammo"]["size_mm"], 8.0)

    def test_new_test_starts_over_and_keeps_the_projectile(self):
        self.ammo()
        self.core.set_settings("a1", ALICE, projectile_mm=5.7)
        self.shoot("3.00", "3.00")
        self.core.set_mode("a1", ALICE, "ammo", new_series=True)
        v = self.view()
        self.assertEqual((v["count"], v["ammo"]["diameter_mm"]), (0, 5.7))
        self.shoot("8.00", "8.00")
        self.assertEqual(self.view()["bull_shots"][0]["decimal"], 10.9)    # a fresh reference
        self.assertEqual(len(self.core.list_series("a1")), 2)

    def test_leaving_ammo_mode_ends_the_test_and_practice_scores_normally(self):
        self.ammo()
        self.shoot("5.00", "5.00")
        self.control("practice")
        self.shoot("0.00", "0.00")
        t = self.core.list_targets()[0]
        self.assertEqual((t["kind"], t["view"]["count"]), ("practice", 1))
        series = self.core.list_series("a1")
        self.assertEqual([g["kind"] for g in series], ["practice", "ammo"])
        self.assertEqual(series[1]["ammo"]["shots"], 1)                 # the finished test is in the review list

    def test_review_of_a_finished_test(self):
        self.ammo()
        self.shoot("1.00", "1.00")
        self.shoot("2.00", "1.00")
        sid = self.view()["series_id"]
        self.control("practice")
        v = self.core.series_view(sid)
        self.assertEqual((v["kind"], v["count"], v["ammo"]["spread_cc_mm"], v["open"]), ("ammo", 2, 1.0, False))

    def test_review_shot_by_shot_shows_the_test_as_it_stood(self):
        self.ammo()
        for xy in [("1.00", "1.00"), ("4.00", "5.00"), ("1.00", "5.00"), ("9.00", "9.00")]:
            self.shoot(*xy)
        sid = self.view()["series_id"]
        self.control("practice")
        sizes = []
        for k in range(0, 7):
            v = self.core.series_view(sid, "scored", None, k)
            self.assertEqual(v["full_count"], 4)
            self.assertEqual((v["count"], len(v["bull_shots"])), (min(k, 4), min(k, 4)))
            sizes.append(v["ammo"].get("size_mm"))
        self.assertEqual(sizes, [None, 4.5, 9.5, 9.5, 15.81, 15.81, 15.81])    # 4.5, then the 3-4-5 pair, then the far shot (8,8) joins
        v3 = self.core.series_view(sid, "scored", None, 3)
        self.assertEqual(v3["last_shot"]["n"], 3)
        self.assertEqual(v3["ammo"]["shots"], 3)

    def test_review_upto_on_a_match_picks_the_right_target_picture(self):
        self.control("record")
        for i in range(25):
            self.shoot("0.00", "0.00")
        sid = self.view()["series_id"]
        for k, (idx, picture, groups, total) in {1: (0, 1, 1, 10.9), 10: (0, 10, 1, 109.0), 11: (1, 1, 2, 119.9), 20: (1, 10, 2, 218.0), 25: (2, 5, 3, 272.5)}.items():
            v = self.core.series_view(sid, "scored", None, k)
            self.assertEqual((v["bull_index"], len(v["bull_shots"]), len(v["groups"]), v["agg"]["decimal"], v["count"], v["full_count"]),
                             (idx, picture, groups, total, k, 25), k)
            self.assertEqual(v["bull_shots"][-1]["n"], k)
        self.assertEqual(self.core.series_view(sid, "scored", None, 999)["count"], 25)
        self.assertEqual(self.core.series_view(sid, "scored")["full_count"], 25)

    def test_ammo_test_of_only_misses_does_not_break_views(self):
        self.ammo()
        self.shoot(raw=MISS % 1)
        v = self.view()
        self.assertEqual((v["count"], v["ammo"]["shots"], "size_mm" in v["ammo"]), (1, 0, False))
        self.assertEqual(self.core.list_series("a1")[0]["ammo"]["shots"], 0)

    def test_empty_ammo_view(self):
        self.ammo()
        v = self.view()
        self.assertEqual((v["kind"], v["count"], v["ammo"]["shots"], v["bull_shots"], v["last_shot"]), ("ammo", 0, 0, [], None))

    def test_projectile_size_validation(self):
        self.control()
        for bad in (0, 0.5, 11, "x", float("nan"), True, [4.5], -1):
            with self.assertRaises(BadRequest, msg=repr(bad)):
                self.core.set_settings("a1", ALICE, projectile_mm=bad)
        self.assertEqual(self.core.set_settings("a1", ALICE, projectile_mm="5.65")["projectile_mm"], 5.66)   # kept even: 0.02 mm steps
        self.assertEqual(even_h(4.5), 450)

    def test_rescore_keeps_ammo_scores_relative(self):
        self.ammo()
        self.shoot("9.00", "9.00")
        self.shoot("9.40", "9.00")
        before = self.core.list_shots("a1")
        self.core.rescore()
        self.assertEqual(self.core.list_shots("a1"), before)
        self.assertEqual(before[0]["decimal"], 10.9)

    def test_ammo_does_not_disturb_other_targets_settings_view(self):
        t = self.core.list_targets()[0]
        self.assertEqual(t["settings"]["projectile_mm"], 4.5)


class UpgradeFromV04(unittest.TestCase):
    def test_series_table_rebuild_keeps_everything(self):
        from fetserver import db as dbmod
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            full = dbmod.MIGRATIONS
            dbmod.MIGRATIONS = full[:4]
            try:
                old = Database(path)
                with old.write() as c:
                    c.execute("INSERT INTO targets (device_id, name, first_seen_ms, last_seen_ms) VALUES ('a1','Lane 1',1,1)")
                    c.execute("INSERT INTO series (device_id, kind, shooter, started_ms, bull_size, group_size, total) "
                              "VALUES ('a1','match','Old',1,10,10,60)")
                    c.execute("INSERT INTO series (device_id, kind, shooter, started_ms, ended_ms, bull_size, group_size, total) "
                              "VALUES ('a1','practice','Older',1,2,10,10,20)")
                old.conn().close()
            finally:
                dbmod.MIGRATIONS = full
            new = Database(path)
            self.assertEqual(new.integrity_check(), "ok")
            rows = new.conn().execute("SELECT id, kind, shooter, total, projectile_h, ended_ms FROM series ORDER BY id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [(1, "match", "Old", 60, 450, None), (2, "practice", "Older", 20, 450, 2)])
            core = Core(new, now_ms=lambda: 1_000_000, lease_ms=10**9)
            core.acquire_control("a1", ALICE, "Alice")
            r = core.set_mode("a1", ALICE, "ammo")
            self.assertEqual((r["kind"], r["series_id"]), ("ammo", 3))          # ids keep counting after the rebuild
            self.assertEqual(core.list_targets()[0]["settings"]["projectile_mm"], 4.5)


if __name__ == "__main__":
    unittest.main()
