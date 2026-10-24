import os
import tempfile
import unittest

from fetserver.core import BadRequest, Core, Forbidden
from fetserver.db import Database
from fetserver.scoring import INNER_TEN_MIN_TENTHS, is_inner_ten, score_xy

ALICE = "alice-client-01"
BOB = "bob-client-0002"
BASE = 1_000_000_000
SHOT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":%s , "N":1, "E":2, "S":3, "W":0 , '
        '"V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }')
MISS = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'


class SeriesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = [BASE]
        self.core = Core(Database(os.path.join(self.tmp.name, "t.db")), now_ms=lambda: self.t[0], lease_ms=10**9)
        self.core.handle_heartbeat({"device_id": "a1", "boot_count": 1, "uptime_ms": 0, "name": "Lane 1"})
        self.seq = 0
        self.ms = 0

    def shoot(self, x="0.00", y="0.00", raw=None, gap=1000):
        self.seq += 1
        self.ms += gap
        self.t[0] = BASE + self.ms
        self.core.handle_events({"device_id": "a1", "boot_count": 1, "uptime_ms": self.ms, "events": [
            {"seq": self.seq, "type": "shot", "uptime_ms": self.ms, "raw": raw or SHOT % (self.seq, x, y)}]})

    def view(self):
        return self.core.list_targets()[0]["view"]

    def control(self, mode=None, **kw):
        self.t[0] = BASE + self.ms
        self.core.acquire_control("a1", ALICE, "Alice")
        if mode:
            return self.core.set_mode("a1", ALICE, mode, **kw)


class InnerTen(unittest.TestCase):
    def test_threshold_is_10_2(self):
        self.assertEqual(INNER_TEN_MIN_TENTHS, 102)
        self.assertFalse(is_inner_ten(101))
        self.assertTrue(is_inner_ten(102))
        self.assertTrue(is_inner_ten(109))

    def test_matches_geometry(self):
        self.assertEqual(score_xy(200, 0).tenths, 102)       # 2.00 mm: pellet just covers the ten dot
        self.assertEqual(score_xy(201, 0).tenths, 101)
        self.assertTrue(is_inner_ten(score_xy(200, 0).tenths))
        self.assertFalse(is_inner_ten(score_xy(201, 0).tenths))


class Practice(SeriesCase):
    def test_first_shot_creates_a_practice_series_without_anyone_in_control(self):
        self.shoot()
        t = self.core.list_targets()[0]
        self.assertEqual((t["kind"], t["mode"], t["view"]["count"], t["view"]["total"]), ("practice", "practice", 1, 20))
        self.assertIsNotNone(t["series_id"])

    def test_numbering_bull_reset_groups_and_totals(self):
        for i in range(10):
            self.shoot("0.00", "0.00")                       # ten 10.9s
        v = self.view()
        self.assertEqual((v["count"], v["bull_index"], len(v["bull_shots"])), (10, 0, 10))
        self.assertEqual([s["n"] for s in v["bull_shots"]], list(range(1, 11)))    # shot 10 still on the target
        self.shoot("0.00", "0.00")                           # shot 11 clears the face
        v = self.view()
        self.assertEqual((v["count"], v["bull_index"], [s["n"] for s in v["bull_shots"]]), (11, 1, [11]))
        self.assertEqual([(g["first"], g["last"], g["decimal"], g["integer"], g["x"], g["complete"]) for g in v["groups"]],
                         [(1, 10, 109.0, 100, 10, True), (11, 11, 10.9, 10, 1, False)])
        self.assertEqual((v["agg"]["decimal"], v["agg"]["integer"], v["agg"]["x"]), (119.9, 110, 11))
        self.assertEqual(v["last_shot"]["n"], 11)

    def test_series_rolls_over_after_the_total(self):
        for i in range(20):
            self.shoot()
        first = self.core.list_targets()[0]["series_id"]
        v = self.view()
        self.assertEqual((v["count"], v["bull_index"]), (20, 1))
        self.assertEqual(len(v["groups"]), 2)
        self.shoot()                                         # the 21st starts a new series at shot 1
        t = self.core.list_targets()[0]
        self.assertNotEqual(t["series_id"], first)
        self.assertEqual((t["view"]["count"], t["view"]["bull_shots"][0]["n"]), (1, 1))
        old = self.core.list_series("a1")[1]
        self.assertEqual((old["id"], old["open"], old["scored"]["shots"]), (first, False, 20))
        self.assertEqual(self.core.series_view(first)["agg"]["shots"], 20)

    def test_idle_practice_series_starts_afresh_but_a_match_does_not(self):
        for i in range(3):
            self.shoot()
        first = self.core.list_targets()[0]["series_id"]
        self.shoot(gap=31 * 60 * 1000)
        self.assertNotEqual(self.core.list_targets()[0]["series_id"], first)
        self.control("record")
        match = self.core.list_targets()[0]["series_id"]
        self.shoot()
        self.shoot(gap=31 * 60 * 1000)
        self.assertEqual(self.core.list_targets()[0]["series_id"], match)

    def test_misses_count_and_take_a_number_but_score_nothing(self):
        self.shoot()
        self.shoot(raw=MISS % 2)
        v = self.view()
        self.assertEqual((v["count"], v["agg"]["decimal"], v["agg"]["misses"], v["agg"]["x"]), (2, 10.9, 1, 1))
        self.assertTrue(v["bull_shots"][1]["miss"])
        self.assertFalse(v["bull_shots"][1]["inner"])

    def test_x_count_uses_10_2_and_above(self):
        for x in ("0.00", "2.00", "2.01", "2.50", "2.51"):   # 10.9, 10.2 (X), 10.1, 10.0, 9.9
            self.shoot(x, "0.00")
        v = self.view()
        self.assertEqual([s["decimal"] for s in v["bull_shots"]], [10.9, 10.2, 10.1, 10.0, 9.9])
        self.assertEqual([s["inner"] for s in v["bull_shots"]], [True, True, False, False, False])
        self.assertEqual(v["agg"]["x"], 2)
        self.assertEqual(v["agg"]["integer"], 10 + 10 + 10 + 10 + 9)


class LateShots(SeriesCase):
    def deliver(self, uptime_at_shot, delivered_at, n):
        self.t[0] = BASE + delivered_at
        self.core.handle_events({"device_id": "a1", "boot_count": 1, "uptime_ms": delivered_at, "events": [
            {"seq": n, "type": "shot", "uptime_ms": uptime_at_shot, "raw": SHOT % (n, "0.00", "0.00")}]})

    def test_a_shot_delivered_late_to_a_full_finished_series_stays_in_it(self):
        self.t[0] = BASE
        self.core.acquire_control("a1", ALICE, "Alice")
        self.core.set_settings("a1", ALICE, total_practice=3)
        practice = self.core.set_mode("a1", ALICE, "practice")["series_id"]
        for i in range(3):
            self.deliver(1000 * (i + 1), 1000 * (i + 1), i + 1)
        self.t[0] = BASE + 10_000
        match = self.core.set_mode("a1", ALICE, "record")["series_id"]          # the practice series ends here
        self.deliver(3500, 11_000, 4)                                           # fired at 3.5 s, uploaded at 11 s
        shot = [x for x in self.core.list_shots("a1") if x["shot_number"] == 4][0]
        self.assertEqual((shot["series_id"], shot["series_no"], shot["stream"]), (practice, 4, "scored"))
        self.assertEqual(self.core.series_view(practice)["count"], 4)
        self.assertEqual(self.core.series_view(match)["count"], 0)
        self.assertEqual(len(self.core.list_series("a1")), 2)                   # no stray series was created

    def test_shot_fired_before_any_mode_is_unassigned_not_a_new_series(self):
        self.t[0] = BASE + 5_000
        self.core.acquire_control("a1", ALICE, "Alice")
        self.core.set_mode("a1", ALICE, "record")
        self.deliver(1000, 6_000, 1)                                            # fired at 1 s, before the claim at 5 s
        shot = self.core.list_shots("a1")[0]
        self.assertEqual((shot["series_id"], shot["series_no"]), (None, None))
        self.assertEqual(len(self.core.list_series("a1")), 1)


class Match(SeriesCase):
    def test_sighters_count_up_without_limit_and_face_resets_every_bull(self):
        self.control("sighter")
        for i in range(25):
            self.shoot()
        v = self.view()
        self.assertEqual((v["stream"], v["count"], v["total"], v["bull_index"], len(v["bull_shots"])), ("sighter", 25, None, 2, 5))
        self.assertEqual(v["bull_shots"][0]["n"], 21)

    def test_record_picks_up_where_it_left_off(self):
        self.control("sighter")
        sid = self.core.list_targets()[0]["series_id"]
        for i in range(3):
            self.shoot()
        self.control("record")
        for i in range(4):
            self.shoot()
        self.control("sighter")
        for i in range(2):
            self.shoot()
        self.control("record")
        for i in range(2):
            self.shoot()
        t = self.core.list_targets()[0]
        self.assertEqual(t["series_id"], sid)
        v = self.view()
        self.assertEqual((v["stream"], v["count"], v["total"], v["bull_shots"][-1]["n"]), ("scored", 6, 60, 6))
        s = self.core.series_view(sid, "sighter")
        self.assertEqual((s["count"], s["bull_shots"][-1]["n"]), (5, 5))

    def test_match_ends_after_60_and_next_shot_starts_another(self):
        self.control("record")
        for i in range(60):
            self.shoot()
        first = self.core.list_targets()[0]["series_id"]
        self.assertEqual(self.view()["count"], 60)
        self.shoot()
        t = self.core.list_targets()[0]
        self.assertNotEqual(t["series_id"], first)
        self.assertEqual((t["mode"], t["kind"], t["view"]["count"]), ("record", "match", 1))

    def test_switching_to_practice_and_back_makes_new_series(self):
        m = self.control("sighter")["series_id"]
        p = self.control("practice")["series_id"]
        m2 = self.control("record")["series_id"]
        self.assertEqual(len({m, p, m2}), 3)


class Settings(SeriesCase):
    def test_requires_control(self):
        with self.assertRaises(Forbidden):
            self.core.set_settings("a1", ALICE, bull_size=5)

    def test_validation(self):
        self.control()
        for bad in ({"bull_size": 0}, {"group_size": 101}, {"total_match": "60"}, {"total_practice": -1}, {"bull_size": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(BadRequest):
                    self.core.set_settings("a1", ALICE, **bad)
        with self.assertRaises(BadRequest):
            self.core.set_settings("a1", ALICE, nonsense=3)

    def test_changing_sizes_applies_to_the_live_series(self):
        for i in range(12):
            self.shoot()
        self.control()
        s = self.core.set_settings("a1", ALICE, bull_size=5, group_size=6, total_practice=15)
        self.assertEqual(s, {"bull_size": 5, "group_size": 6, "total_practice": 15, "total_match": 60})
        v = self.view()
        self.assertEqual((v["bull_size"], v["group_size"], v["total"], v["bull_index"], v["bull_count"]), (5, 6, 15, 2, 3))
        self.assertEqual([s["n"] for s in v["bull_shots"]], [11, 12])
        self.assertEqual([(g["first"], g["last"]) for g in v["groups"]], [(1, 6), (7, 12)])
        for i in range(3):
            self.shoot()
        self.shoot()                                         # 16th shot: past the new total of 15
        self.assertEqual(self.view()["count"], 1)

    def test_new_series_use_the_target_settings(self):
        self.control()
        self.core.set_settings("a1", ALICE, bull_size=4, total_match=30)
        m = self.control("sighter")
        v = self.view()
        self.assertEqual((v["bull_size"], v["total"], v["series_id"]), (4, None, m["series_id"]))   # sighters have no total
        self.assertEqual(self.core.series_view(m["series_id"], "scored")["total"], 30)


class Views(SeriesCase):
    def test_review_pages_and_unknown_series(self):
        self.control("record")
        for i in range(23):
            self.shoot()
        sid = self.core.list_targets()[0]["series_id"]
        p0 = self.core.series_view(sid, "scored", 0)
        p2 = self.core.series_view(sid, "scored", 2)
        self.assertEqual((p0["bull_count"], len(p0["bull_shots"]), p0["bull_shots"][0]["n"]), (3, 10, 1))
        self.assertEqual((len(p2["bull_shots"]), p2["bull_shots"][0]["n"]), (3, 21))
        self.assertEqual(self.core.series_view(sid, "scored", 99)["bull_index"], 2)       # clamped
        with self.assertRaises(BadRequest):
            self.core.series_view(sid, "nope")
        with self.assertRaises(LookupError):
            self.core.series_view(999)

    def test_series_list_has_both_streams(self):
        self.control("sighter")
        for i in range(2):
            self.shoot("0.00", "0.00")
        self.control("record")
        self.shoot("2.00", "0.00")
        item = self.core.list_series("a1")[0]
        self.assertEqual((item["sighter"]["shots"], item["scored"]["shots"], item["scored"]["decimal"], item["scored"]["x"]),
                         (2, 1, 10.2, 1))


if __name__ == "__main__":
    unittest.main()
