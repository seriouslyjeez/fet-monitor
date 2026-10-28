"""Time rules: interruptions of one shooter or the whole range (ISSF 6.11.3, 6.10.9.1; CMP 7.18.1-4; NRA 9.2 b),
malfunctions (ISSF 6.13.4, CMP 7.19.1, NRA 9.5) and late arrival (ISSF 6.11.4)."""
from fetserver.matches import MatchError, interruption_time, range_interruption
from tests.test_matches import MatchCase


class Rules(MatchCase):
    def test_interruption_time_by_rulebook(self):
        L = 500_000                                            # what they had left when it began
        cases = [(("ISSF", "interrupt", L, 100), (L - 100_000, False, "ISSF 6.11.3.1")),     # up to 3 min: not given back
                 (("ISSF", "interrupt", L, 200), (L, False, "ISSF 6.11.3.1")),               # the time lost
                 (("ISSF", "interrupt", 240_000, 200), (300_000, False, "ISSF 6.11.3.1")),   # in the last 5 min: + 1 min
                 (("ISSF", "interrupt", L, 400), (L + 300_000, True, "ISSF 6.11.3.2")),
                 (("CMP", "interrupt", L, 100), (L, False, "CMP 7.18.1")),
                 (("CMP", "interrupt", L, 400), (L + 120_000, True, "CMP 7.18.2")),
                 (("NRA", "interrupt", L, 100), (L - 100_000, False, "NRA 9.2 b")),
                 (("NRA", "interrupt", L, 400), (L, True, "NRA 9.2 b")),
                 (("NRA", "malfunction", L, 900), (L, True, "NRA 9.5"))]
        for args, want in cases:
            self.assertEqual(interruption_time(*args), want, args)
        self.assertEqual(range_interruption("ISSF", 400), (300, True, "ISSF 6.10.9.1, 6.11.3.2"))
        self.assertEqual(range_interruption("CMP", 400), (120, True, "CMP 7.18.4"))
        self.assertEqual(range_interruption("NRA", 400), (0, True, "NRA 9.2 b"))
        self.assertEqual(range_interruption("CMP", 200)[:2], (0, False))


class OneShooter(MatchCase):
    def setUp(self):
        super().setUp()
        self.mid = self.m.create("M", self.custom([("record", 600, 5)]), [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "dir")
        self.m.start(self.mid, "dir")

    def lane(self, did="lane-1"):
        return next(l for l in self.m.detail(self.mid)["lanes"] if l["device_id"] == did)

    def test_interruption_gives_back_the_time_lost(self):
        self.at(100); self.m.lane_command(self.mid, "lane-1", "interrupt", "ro")
        self.assertEqual((self.lane()["own"], self.lane()["stop"]), ("moving", "interrupt"))
        self.at(200); self.shoot("lane-1")
        self.at(400); self.m.lane_command(self.mid, "lane-1", "interrupt_end", "ro")      # 300 s lost: given back
        self.assertEqual((self.lane()["own_remaining_ms"], self.mode()), (500_000, "record"), "no sighters for 5 minutes or less")
        self.assertEqual(self.flags(self.mid), [("lane-1", 1, "scored", "during an interruption")])
        with self.assertRaises(MatchError):
            self.m.lane_command(self.mid, "lane-1", "interrupt_end", "ro")

    def test_over_five_minutes_extra_time_and_sighters(self):
        self.at(100); self.m.lane_command(self.mid, "lane-1", "interrupt", "ro")
        self.at(500); self.m.lane_command(self.mid, "lane-1", "interrupt_end", "ro")      # ISSF 6.11.3.2
        self.assertEqual((self.lane()["own_remaining_ms"], self.mode(), self.lane()["sighting"]), (800_000, "sighter", True))
        self.at(510); self.shoot("lane-1")                                                # a sighter: allowed
        self.m.lane_command(self.mid, "lane-1", "start_record", "ro")
        self.at(520); self.shoot("lane-1")
        self.assertEqual(self.flags(self.mid), [])

    def test_a_move_is_not_ended_by_interruption_end(self):
        self.core.handle_heartbeat({"device_id": "lane-3", "boot_count": 1, "uptime_ms": 0})
        self.at(100); self.m.move(self.mid, "lane-1", "lane-3", "ro")
        with self.assertRaises(MatchError):
            self.m.lane_command(self.mid, "lane-3", "interrupt_end", "ro")
        with self.assertRaises(MatchError):
            self.m.lane_command(self.mid, "lane-3", "interrupt", "ro")                  # already stopped

    def test_malfunction_issf(self):
        self.at(100); self.m.lane_command(self.mid, "lane-1", "malfunction", "ro")       # not allowable: nothing changes
        self.assertEqual((self.lane()["own"], self.mode()), ("", "record"))
        self.m.lane_command(self.mid, "lane-1", "malfunction", "ro", seconds=1)          # allowable: sighters (6.13.4)
        self.assertEqual((self.lane()["own"], self.mode(), self.lane()["sighting"]), ("", "sighter", True))


class Malfunctions(MatchCase):
    def run_one(self, fid, at):
        mid = self.m.create("M", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "dir")
        while self.m.detail(mid)["clock"]["kind"] not in ("record", "stage"):
            self.m.command(mid, "next", "dir")
        start = self.clock.t
        self.clock.t = start + at * 1000
        self.m.lane_command(mid, "lane-1", "malfunction", "ro")
        return mid, next(l for l in self.m.detail(mid)["lanes"])

    def test_cmp_two_minutes_after_the_five_minute_warning(self):
        mid, ln = self.run_one(self.m.save_format({"name": "CMPish", "tie_rules": "CMP", "phases": [{"kind": "record", "label": "R", "seconds": 600, "shots": 5}]}, "d")["id"], 400)
        self.assertEqual((ln["own"], ln["sighting"]), ("extra", True), "200 s left: +2 min (7.19.1), sighters on request")
        self.assertEqual(ln["own_remaining_ms"], 320_000)

    def test_nra_disabled_rifle_stops_the_clock(self):
        mid, ln = self.run_one("nra-s40", 100)
        self.assertEqual((ln["own"], ln["stop"]), ("moving", "malfunction"))
        left = ln["own_remaining_ms"]
        self.clock.t += 900_000
        self.m.lane_command(mid, "lane-1", "interrupt_end", "ro")
        ln = self.m.detail(mid)["lanes"][0]
        self.assertEqual((ln["own_remaining_ms"], ln["sighting"]), (left, True), "the time that remained, sighters (9.5, 9.2 c)")


class WholeRange(MatchCase):
    def setUp(self):
        super().setUp()
        self.mid = self.m.create("M", self.custom([("record", 600, 5)]), [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "dir")
        self.m.start(self.mid, "dir")

    def test_over_five_minutes(self):
        self.at(100); self.m.command(self.mid, "interrupt", "ro")
        with self.assertRaises(MatchError):
            self.m.command(self.mid, "interrupt", "ro")
        self.at(500); self.m.command(self.mid, "interrupt_end", "ro")
        d = self.m.detail(self.mid)
        self.assertEqual((d["state"], d["clock"]["remaining_ms"]), ("running", 800_000), "500 s left + 5 min (ISSF 6.10.9.1)")
        self.assertEqual((self.mode("lane-1"), self.mode("lane-2")), ("sighter", "sighter"))
        self.at(510); self.shoot("lane-2")
        self.m.command(self.mid, "start_record", "ro", device_id="lane-2")
        self.at(520); self.shoot("lane-2")
        self.assertEqual(self.flags(self.mid), [], "sighters allowed for everyone until they start record")

    def test_short_and_not_a_plain_pause(self):
        self.at(100); self.m.command(self.mid, "pause", "ro")
        with self.assertRaises(MatchError):
            self.m.command(self.mid, "interrupt_end", "ro")                                # a plain pause
        self.m.command(self.mid, "resume", "ro")
        self.m.command(self.mid, "interrupt", "ro")
        self.at(200); self.m.command(self.mid, "interrupt_end", "ro")
        self.assertEqual((self.m.detail(self.mid)["clock"]["remaining_ms"], self.mode("lane-1")), (500_000, "record"))


class LateArrival(MatchCase):
    def test_joins_without_extra_time(self):
        self.core.handle_heartbeat({"device_id": "lane-3", "boot_count": 1, "uptime_ms": 0})
        cc = self.core.competitions
        cid = cc.create("L", self.custom([("sight", 100, 0), ("record", 300, 2)]), "md")
        a, b = cc.save_competitor(cid, {"family": "Ames"}), cc.save_competitor(cid, {"family": "Bell"})
        rid = cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}], "md")
        self.m.start(rid, "md")
        self.at(150)                                                                     # in record
        cc.add_late(rid, b, "lane-3", "md")
        lane = next(l for l in self.m.detail(rid)["lanes"] if l["device_id"] == "lane-3")
        self.assertEqual((lane["competitor"], lane["own"], lane["sighting"], self.mode("lane-3")), ("BELL", "", True, "sighter"))
        self.at(160); self.shoot("lane-3")                                               # a sighter: allowed (6.11.4)
        self.m.command(rid, "start_record", "ro", device_id="lane-3")
        self.at(170); self.shoot("lane-3")
        self.assertEqual(self.flags(rid), [])
        self.assertEqual(next(e for cl in cc.results(cid)["classes"] for e in cl["individuals"] if e["family"] == "Bell")["shots"], 1)
        for args in ((b, "lane-2"), (a, "lane-2"), (999, "lane-2")):                    # already on a relay; unknown
            with self.assertRaises(MatchError, msg=args):
                cc.add_late(rid, *args, "md")
        with self.assertRaises(MatchError):
            self.m.add_late(rid, "lane-1", "X", "md")                                   # a target in the match
