"""Moving a shooter to another target (ISSF 6.10.9.2 / 6.11.3.2, CMP 7.18.3, NRA 9.2 b / 10.3.2): their shots go with
them, their own time stands still while they move, then runs with the rulebook's extra time; sighters are allowed
until they start record again; they finish their phase on their own clock when the match has gone on."""
from fetserver.matches import MatchError
from tests.test_matches import BASE, SHOT, MatchCase


class Moves(MatchCase):
    def setUp(self):
        super().setUp()
        self.core.handle_heartbeat({"device_id": "lane-3", "boot_count": 1, "uptime_ms": 0})     # the reserve target

    def lane(self, mid, did):
        return next(l for l in self.m.detail(mid)["lanes"] if l["device_id"] == did)

    def match(self, kinds, name="M"):
        mid = self.m.create(name, self.custom(kinds), [{"device_id": "lane-1", "competitor": "Ames"}, {"device_id": "lane-2", "competitor": "Bell"}], "dir")
        self.m.start(mid, "dir")
        return mid

    def record_count(self, mid, did):
        r = self.m.results(mid)
        return next(x for x in r["rows"] if x["device_id"] == did)["shots"]

    def test_shots_and_time_go_with_the_shooter(self):
        mid = self.match([("sight", 100, 0), ("record", 600, 6)])
        self.at(110); self.shoot("lane-1"); self.at(120); self.shoot("lane-1")
        self.at(200)
        self.m.move(mid, "lane-1", "lane-3", "ro")
        ln = self.lane(mid, "lane-3")
        self.assertEqual((ln["own"], ln["own_remaining_ms"], ln["competitor"], ln["moved_from"]), ("moving", 500_000, "Ames", ["lane-1"]))
        self.assertEqual((self.mode("lane-1"), self.mode("lane-3")), ("practice", "sighter"), "the old target leaves the match")
        left = next(t for t in self.core.list_targets() if t["device_id"] == "lane-1")["view"]
        self.assertNotEqual(left["series_id"], ln["series_id"], "the old target starts a practice series of its own")
        self.at(230); self.shoot("lane-3")                       # fired while moving: flagged
        self.at(260)
        self.assertEqual(self.lane(mid, "lane-3")["own_remaining_ms"], 500_000, "their time stands still while they move")
        self.m.lane_command(mid, "lane-3", "ready", "ro")        # custom formats: ISSF rules, 5 minutes extra
        ln = self.lane(mid, "lane-3")
        self.assertEqual((ln["own"], ln["own_remaining_ms"], ln["sighting"]), ("extra", 800_000, True), "500 s left + 300 s")
        self.at(270); self.shoot("lane-3")                       # unlimited sighters after a move
        self.at(280); self.m.lane_command(mid, "lane-3", "start_record", "ro")
        self.assertEqual(self.mode("lane-3"), "record")
        for t in (290, 300, 310):
            self.at(t); self.shoot("lane-3")
        self.at(300); self.shoot("lane-1")                       # the old target is no longer theirs
        self.assertEqual(self.record_count(mid, "lane-3"), 5, "two shots on the old target, three on the new one")
        view = next(t for t in self.core.list_targets() if t["device_id"] == "lane-3")["view"]
        self.assertEqual(view["series_id"], ln["series_id"], "the new target shows the shooter's series")
        self.assertIn(ln["series_id"], [x["id"] for x in self.core.list_series("lane-3")], "listed with the new target")
        self.at(700)                                             # the match time is over: Ames has 360 s of their own
        self.assertEqual(self.m.detail(mid)["state"], "overtime")
        self.assertEqual((self.mode("lane-2"), self.mode("lane-3")), ("sighter", "record"))
        self.at(800); self.shoot("lane-3")                       # the sixth record shot, in their own time
        self.at(1059)
        self.assertEqual(self.m.detail(mid)["state"], "overtime")
        self.at(1061)
        d = self.m.detail(mid)
        self.assertEqual((d["state"], d["ended_ms"], self.mode("lane-3")), ("done", self.clock.t - 1000, "sighter"), "ends at 1060 exactly")
        self.at(1070); self.shoot("lane-3")
        self.assertEqual(self.record_count(mid, "lane-3"), 6)
        self.assertEqual([(d, n, s, w) for d, n, s, w in self.flags(mid) if d == "lane-3"],
                         [("lane-3", 1, "sighter", "while moving to another target"), ("lane-3", 3, "sighter", "after the end")])

    def test_moved_in_sighting_finishes_it_on_their_own(self):
        mid = self.match([("sight", 100, 0), ("record", 300, 2)])
        self.at(50)
        self.m.move(mid, "lane-1", "lane-3", "ro")               # 50 s of sighting left
        self.at(120)
        self.assertEqual((self.mode("lane-2"), self.mode("lane-3")), ("record", "sighter"), "the others go on to record")
        self.m.lane_command(mid, "lane-3", "ready", "ro", seconds=0)
        info = next(t for t in self.core.list_targets() if t["device_id"] == "lane-3")["match"]
        self.assertEqual((info["remaining_ms"], info["kind"]), (50_000, "sight"))
        self.assertIn("your own time", info["label"])
        self.at(160); self.shoot("lane-3")                       # a sighter in their own sighting time
        self.at(171)
        self.assertEqual(self.mode("lane-3"), "record", "their sighting ended at 170: they join record")
        self.at(180); self.shoot("lane-3")
        self.assertEqual(self.flags(mid), [])
        self.assertEqual(self.record_count(mid, "lane-3"), 1)

    def test_a_pause_stops_their_own_time_too(self):
        mid = self.match([("record", 100, 3)])
        self.at(50); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.at(60); self.m.lane_command(mid, "lane-3", "ready", "ro", seconds=10)   # 50 + 10 s: 20 s past the match
        self.at(100)
        self.assertEqual(self.m.detail(mid)["state"], "overtime")
        self.at(105); self.m.command(mid, "pause", "ro")
        self.at(200)
        self.assertEqual(self.lane(mid, "lane-3")["own_remaining_ms"], 15_000)
        self.m.command(mid, "resume", "ro")
        self.at(214)
        self.assertEqual(self.m.detail(mid)["state"], "overtime")
        self.at(215)
        self.assertEqual(self.m.detail(mid)["state"], "done")

    def test_a_pause_in_the_match_stops_their_own_phase(self):
        mid = self.match([("sight", 100, 0), ("record", 300, 2)])
        self.at(50); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.at(120); self.m.lane_command(mid, "lane-3", "ready", "ro", seconds=0)   # own sighting until 170
        self.at(130); self.m.command(mid, "pause", "ro")
        self.at(500); self.m.command(mid, "resume", "ro")
        self.assertEqual(self.lane(mid, "lane-3")["own_remaining_ms"], 40_000)
        self.at(539)
        self.assertEqual(self.mode("lane-3"), "sighter")
        self.at(540)
        self.assertEqual(self.mode("lane-3"), "record")

    def test_a_shot_while_the_match_is_paused_in_their_own_time(self):
        mid = self.match([("sight", 100, 0), ("record", 300, 2)])
        self.at(50); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.at(120); self.m.lane_command(mid, "lane-3", "ready", "ro", seconds=0)   # own sighting until 170
        self.at(130); self.m.command(mid, "pause", "ro")
        self.at(140); self.shoot("lane-3")
        self.assertEqual(self.flags(mid), [("lane-3", 1, "sighter", "while the clock was paused")])

    def test_officer_ends_the_overtime(self):
        mid = self.match([("record", 100, 3)])
        self.at(50); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.at(100)
        self.assertEqual(self.m.detail(mid)["state"], "overtime", "still moving: the match waits for them")
        with self.assertRaises(MatchError):
            self.m.command(mid, "extra", "ro", seconds=60)
        self.m.command(mid, "next", "ro")
        self.assertEqual((self.m.detail(mid)["state"], self.mode("lane-3")), ("done", "sighter"))

    def test_restart_puts_everyone_back_on_the_match_clock(self):
        mid = self.match([("sight", 100, 0), ("record", 300, 2)])
        self.at(150); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.m.restart(mid, 1, "dir")
        self.assertEqual((self.lane(mid, "lane-3")["own"], self.mode("lane-3")), ("", "record"))

    def test_rulebook_extra_time(self):
        for fid, extra in (("cmp-3x20", 120), ("cmp-ag-ar60", 120), ("nra-3x20", 300), ("issf-ar60", 300)):
            mid = self.m.create(fid, fid, [{"device_id": "lane-1"}], "dir")
            self.m.start(mid, "dir")
            self.assertEqual(self.m.detail(mid)["move_extra_s"], extra, fid)
            self.m.command(mid, "abort", "dir")

    def test_what_cannot_be_moved(self):
        mid = self.match([("record", 100, 3)])
        for frm, to in (("lane-1", "lane-2"), ("lane-1", "lane-1"), ("lane-1", "nope"), ("lane-9", "lane-3")):
            with self.assertRaises(MatchError, msg=(frm, to)):
                self.m.move(mid, frm, to, "ro")
        with self.assertRaises(MatchError):
            self.m.lane_command(mid, "lane-1", "ready", "ro")     # not moving
        self.at(50); self.m.move(mid, "lane-1", "lane-3", "ro")
        with self.assertRaises(MatchError):
            self.m.lane_command(mid, "lane-3", "start_record", "ro")   # still moving
        self.m.lane_command(mid, "lane-3", "ready", "ro", seconds=0)
        self.at(100)                                              # lane-2 finished; lane-3 had no time beyond the match
        self.assertEqual(self.m.detail(mid)["state"], "done")
        with self.assertRaises(MatchError):
            self.m.move(mid, "lane-3", "lane-1", "ro")

    def test_before_the_start_only_the_target_changes(self):
        mid = self.m.create("M", self.custom([("record", 100, 3)]), [{"device_id": "lane-1", "competitor": "Ames"}], "dir")
        self.m.move(mid, "lane-1", "lane-3", "dir")
        self.m.start(mid, "dir")
        self.assertEqual((self.mode("lane-3"), self.mode("lane-1")), ("record", "practice"))
        self.assertEqual(self.lane(mid, "lane-3")["own"], "")

    def test_nra_stage_moved_after_record_started(self):
        mid = self.match([("stage", 600, 3)])
        self.at(10); self.shoot("lane-1")                         # a sighter
        self.m.command(mid, "start_record", "ro", device_id="lane-1")
        self.at(20); self.shoot("lane-1")                         # record 1
        self.at(30); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.at(40); self.m.lane_command(mid, "lane-3", "ready", "ro")
        self.assertFalse(self.lane(mid, "lane-3")["in_record"])
        self.at(50); self.shoot("lane-3")                         # sighters again after the move (NRA 9.2 b)
        self.m.command(mid, "start_record", "ro", device_id="lane-3")
        self.at(60); self.shoot("lane-3")                         # record 2
        self.assertEqual(self.mode("lane-3"), "record")
        self.assertEqual(self.flags(mid), [])
        self.assertEqual(self.record_count(mid, "lane-3"), 2)
        with self.assertRaises(MatchError):
            self.m.command(mid, "start_record", "ro", device_id="lane-3")

    def test_a_late_upload_from_the_old_target_still_counts(self):
        mid = self.match([("record", 600, 3)])
        self.at(100); self.m.move(mid, "lane-1", "lane-3", "ro")
        self.clock.t += 5000                                      # a shot fired at 90 s arrives after the move
        self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": self.clock.t - BASE, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": 90_000, "raw": SHOT % (1, "1.00")}]})
        self.assertEqual(self.record_count(mid, "lane-3"), 1)
