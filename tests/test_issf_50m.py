"""ISSF 50 m rifle and 10 m air pistol qualifications (ISSF Rule Book 2026: 6.11.1, 6.11.9.1-3, 7.7), and the
3-position switch between positions on one clock (7.7.3)."""
import unittest

from fetserver.matchformats import BUILTIN, blocks, final_fits, validate
from fetserver.matches import MatchError
from tests.test_matches import MatchCase

F = {k: validate(BUILTIN[k]) for k in ("issf-r3p-in", "issf-r3p-out", "issf-r50p", "issf-ap60", "issf-fp60", "cmp-ag-ap60")}


class Courses(unittest.TestCase):
    def test_times_and_faces(self):
        ph = lambda k: [(p["kind"], p["seconds"]) for p in F[k]["phases"]]
        self.assertEqual(ph("issf-r3p-in"), [("setup", 600), ("sight", 900), ("pause", 30), ("record", 5400)], "6.11.1.1; 7.7.4 indoor 1:30")
        self.assertEqual(ph("issf-r3p-out")[-1], ("record", 6300), "outdoor 1:45")
        self.assertEqual(ph("issf-r50p")[-1], ("record", 3000), "prone: 50 minutes (6.11.9.3)")
        self.assertEqual(ph("issf-ap60")[-1], ("record", 4500), "air pistol: 1 hour 15 (6.11.9.1)")
        self.assertEqual({k: (f["face"], f["scoring"]) for k, f in F.items()},
                         {"issf-r3p-in": ("issf-r50", "integer"), "issf-r3p-out": ("issf-r50", "integer"),
                          "issf-r50p": ("issf-r50", "decimal"), "issf-ap60": ("issf-ap10", "integer"),
                          "issf-fp60": ("issf-p25", "integer"), "cmp-ag-ap60": ("issf-ap10", "integer")})
        self.assertEqual(ph("issf-fp60")[-1], ("record", 5400), "50 m pistol: 1 hour 30 (6.11.9.8)")
        self.assertEqual(ph("cmp-ag-ap60"), [("setup", 900), ("sight", 900), ("record", 4500)], "CMP AG 6.4.3 b, as AR60")
        self.assertEqual((F["cmp-ag-ap60"]["book"], F["cmp-ag-ap60"]["tie_rules"]), ("CMP-AG", "CMP"), "ties: CMP AG 8.5.1 c")
        self.assertTrue(final_fits("airgun", F["cmp-ag-ap60"]) and final_fits("issf", F["issf-ap60"]), "each has its 10 m final")
        rec = F["issf-r3p-in"]["phases"][3]
        self.assertEqual([q["position"] for q in rec["positions"]], ["kneeling", "prone", "standing"], "7.7.1")
        self.assertEqual(blocks(rec), [20, 40, 60])
        self.assertEqual(F["issf-r3p-in"]["phases"][1]["position"], "kneeling", "sighting time: the first position")
        self.assertEqual([w["at"] for w in rec["warnings"]], [600, 300], "6.11.1.2 e")

    def test_positions_must_add_up(self):
        bad = dict(BUILTIN["issf-r3p-in"])
        bad["phases"] = [dict(p) for p in bad["phases"]]
        bad["phases"][3]["positions"] = [{"position": "kneeling", "shots": 20}, {"position": "prone", "shots": 20}]
        with self.assertRaisesRegex(ValueError, "add up"):
            validate(bad)


class ThreePositions(MatchCase):
    def start(self):
        mid = self.m.create("50 m", "issf-r3p-in", [{"device_id": "lane-1", "competitor": "Al Ames"},
                                                    {"device_id": "lane-2", "competitor": "Bo Bell"}], "md")
        self.m.start(mid, "md")
        self.at(600 + 900 + 30 + 1)                          # MATCH firing: kneeling
        return mid

    def lane(self, mid, did):
        return next(l for l in self.m.detail(mid)["lanes"] if l["device_id"] == did)

    def streams(self, did):
        return [r[0] for r in self.core.db.conn().execute("SELECT stream FROM shots WHERE device_id=? ORDER BY id", (did,))]

    def test_the_switch_between_positions(self):
        mid = self.start()
        t = 1600
        for _ in range(19):
            self.at(t); t += 1
            self.shoot("lane-1")
        self.assertEqual((self.lane(mid, "lane-1")["position"], self.lane(mid, "lane-1")["pos_shots"]), ("kneeling", 19))
        with self.assertRaisesRegex(MatchError, "finish the kneeling shots first"):
            self.m.command(mid, "back_to_sighters", "ro", device_id="lane-1")
        self.at(t); t += 1
        self.shoot("lane-1")                                  # the 20th kneeling shot
        self.assertTrue(self.lane(mid, "lane-1")["can_back"])
        self.m.command(mid, "back_to_sighters", "ro", device_id="lane-1")
        self.assertEqual(self.mode("lane-1"), "sighter")
        l = self.lane(mid, "lane-1")
        self.assertEqual((l["between"], l["position"]), (True, "prone"), "sighting for the next position")
        self.m.command(mid, "pause", "ro")
        self.m.command(mid, "resume", "ro")
        self.assertEqual(self.mode("lane-1"), "sighter", "a resume keeps the lane sighting (it switches itself back, 7.7.3)")
        with self.core.db.write() as c:                       # anything that puts the lanes in record (a moved shooter rejoining, ...)
            self.m._set_modes(c, self.m._row(c, mid), "record", self.clock.t)
        self.assertEqual((self.mode("lane-1"), self.mode("lane-2")), ("sighter", "record"), "only a lane between positions stays sighting")
        for _ in range(3):                                    # unlimited prone sighters, no extra time
            self.at(t); t += 1
            self.shoot("lane-1")
        self.m.command(mid, "start_record", "ro", device_id="lane-1")
        self.assertEqual(self.mode("lane-1"), "record")
        for _ in range(20):
            self.at(t); t += 1
            self.shoot("lane-1")
        self.m.command(mid, "back_to_sighters", "ro", device_id="lane-1")      # after prone: sighters for standing
        self.at(t); t += 1
        self.shoot("lane-1")
        self.m.command(mid, "start_record", "ro", device_id="lane-1")
        for _ in range(20):
            self.at(t); t += 1
            self.shoot("lane-1")
        with self.assertRaisesRegex(MatchError, "standing is the last position"):
            self.m.command(mid, "back_to_sighters", "ro", device_id="lane-1")
        self.assertEqual(self.streams("lane-1"), ["scored"] * 20 + ["sighter"] * 3 + ["scored"] * 20 + ["sighter"] + ["scored"] * 20)
        self.assertEqual([f for f in self.flags(mid) if f[0] == "lane-1"], [], "sighters between positions are allowed (7.7.3)")
        self.assertEqual(self.lane(mid, "lane-1")["record"], 60)

    def test_an_extra_shot_in_the_previous_position(self):
        mid = self.start()
        for i in range(21):                                   # the athlete forgets to switch after kneeling
            self.at(1600 + i)
            self.shoot("lane-2")
        flags = self.m.detail(mid)["flags"]
        self.assertEqual([(f["n"], f["code"]) for f in flags], [(21, "extra_in_position")])
        g = flags[0]["guide"]
        self.assertEqual((g["rule"], g["suggest"]), ("ISSF 7.7.3", "annul"), "nullified, target reset to sighting")
        self.at(1650)                                         # a moment later, the range officer resets it
        self.m.command(mid, "back_to_sighters", "ro", device_id="lane-2")      # the reset
        self.m.command(mid, "start_record", "ro", device_id="lane-2")
        self.at(1700)
        self.shoot("lane-2")
        self.assertEqual(len(self.m.detail(mid)["flags"]), 1, "the first prone shot counts")

    def test_the_athlete_switches_on_their_own_screen(self):
        mid = self.start()
        for i in range(20):
            self.at(1600 + i)
            self.shoot("lane-1")
        self.core.acquire_control("lane-1", "client-000001", "Al")
        info = self.core.shooter_back_to_sighters("lane-1", "client-000001")
        self.assertEqual((info["between"], info["position"]), (True, "prone"))
        info = self.core.shooter_start_record("lane-1", "client-000001")
        self.assertEqual((info["between"], info["position"], info["pos_shots"]), (False, "prone", 0))
        self.assertIsNotNone(mid)


class OtherBooksUnchanged(MatchCase):
    def test_one_position_record_has_no_switch(self):
        mid = self.m.create("Prone", "issf-r50p", [{"device_id": "lane-1"}], "md")
        self.m.start(mid, "md")
        self.at(1531)
        self.shoot("lane-1")
        with self.assertRaisesRegex(MatchError, "no sighters once record has started|sighting-and-record stage"):
            self.m.command(mid, "back_to_sighters", "ro", device_id="lane-1")
        self.assertNotIn("position", self.m.detail(mid)["lanes"][0])


if __name__ == "__main__":
    unittest.main()
