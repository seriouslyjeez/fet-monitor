"""NRA Smallbore Rifle Rules 2026 in Match Control: the built-in courses of fire (7.2-7.5, time 8.1.1 / 8.2,
preparation 10.3.1, position changes 8.2, no warnings 8.4), their faces, and a match fired to its results with the
smallbore tie-breaks (15.7) and its own procedures."""
import unittest

from fetserver import faces as F
from fetserver import rulebooks as rb
from fetserver.matchformats import BUILTIN, SB_POS_S, record_shots, validate
from tests.test_matches import MatchCase

SB = {k: validate(f) for k, f in BUILTIN.items() if k.startswith("nra-sb-")}


class Courses(unittest.TestCase):
    def test_every_course_follows_the_rulebook(self):
        self.assertEqual(len(SB), 20)
        for fid, f in SB.items():
            with self.subTest(fid):
                self.assertEqual((f["book"], f["tie_rules"], f["scoring"]), (rb.SB, "NRA-SB", "integer"))
                self.assertFalse(F.FACES[f["face"]].decimal and f["face"].startswith("nra-a1"), "conventional faces are whole rings")
                self.assertEqual((f["phases"][0]["kind"], f["phases"][0]["seconds"]), ("setup", 180), "10.3.1: 3 minutes preparation")
                self.assertFalse(any(p["warnings"] for p in f["phases"]), "8.4: no time warnings")
                for p in f["phases"]:
                    if p["kind"] == "changeover":
                        self.assertEqual(p["seconds"], 300, "8.2: 5 minutes per position change")
                self.assertTrue(all(p["kind"] in ("setup", "stage", "changeover") for p in f["phases"]), "sighters within the stage (9.2)")

    def test_times(self):
        st = lambda fid: [(p["position"], p["seconds"] // 60, p["shots"]) for p in SB[fid]["phases"] if p["kind"] == "stage"]
        self.assertEqual(st("nra-sb-a17-4x10"), [("prone", 10, 10), ("sitting", 10, 10), ("kneeling", 10, 10), ("standing", 15, 10)],
                         "8.2 a: indoor, 1 minute a shot, 1 1/2 standing")
        self.assertEqual(st("nra-sb-a23-4x20"), [("prone", 20, 20), ("sitting", 30, 20), ("kneeling", 30, 20), ("standing", 30, 20)],
                         "8.2 b: outdoor, 1 minute prone, 1 1/2 the others")
        self.assertEqual(st("nra-sb-a50-3x20"), [("prone", 20, 20), ("standing", 40, 20), ("kneeling", 30, 20)],
                         "8.2 c: NRA metric position, 1 / 2 / 1 1/2 minutes; prone, standing, kneeling (7.5)")
        self.assertEqual(st("nra-sb-a23-p40"), [("prone", 45, 40)], "8.1.1: one 40-shot stage on e-targets, 45 minutes")
        self.assertEqual(st("nra-sb-a50-p60"), [("prone", 65, 60)], "8.1.1: 60 shots, 65 minutes")
        self.assertEqual(st("nra-sb-a23-p20"), [("prone", 20, 20)])
        self.assertEqual(SB_POS_S["c"]["standing"], 120)

    def test_faces(self):
        face = {fid: f["face"] for fid, f in SB.items()}
        self.assertEqual({face[k] for k in face if "-a17-" in k}, {"nra-a17"}, "50 ft conventional: A-17 (7.3)")
        self.assertEqual({face[k] for k in face if "-a23-" in k}, {"nra-a23"}, "50 yd conventional: A-23 (7.2, 7.3)")
        self.assertEqual({face[k] for k in face if "-a50-" in k}, {"issf-r50"}, "A-50 is the ISSF 50 m target (4.10)")
        self.assertEqual({face[k] for k in face if "usas50" in k}, {"nra-usas50"}, "50 ft metric position (7.5)")
        self.assertEqual(record_shots(SB["nra-sb-usas50-3x40"]), 120)


class Fired(MatchCase):
    def test_a_prone_match_to_its_results(self):
        mid = self.m.create("Club 50 yd", "nra-sb-a23-p20", [{"device_id": "lane-1", "competitor": "Al Ames"},
                                                             {"device_id": "lane-2", "competitor": "Bo Bell"}], "md")
        d = self.m.detail(mid)
        self.assertEqual((d["face"]["id"], d["rb"]["book"]), ("nra-a23", rb.SB))
        self.m.start(mid, "md")
        self.at(181)                                       # after the 3-minute preparation: the stage
        for did in ("lane-1", "lane-2"):
            self.m.command(mid, "start_record", "md", device_id=did)
        for i in range(20):
            self.at(200 + i)
            self.shoot("lane-1", "1.00")                   # 20 X's
            self.shoot("lane-2", "1.00" if i else "10.00")  # 19 X's and a ten (X ring 7.80 mm with the .224 gauge)
        res = self.m.results(mid)
        rows = [(r["competitor"], r["place"], r["total"], r["x"]) for r in res["rows"]]
        self.assertEqual(rows[0][:2], ("Al Ames", 1), rows)
        self.assertEqual(rows[1][:2], ("Bo Bell", 2), "same 200, fewer X's (15.7.1 a 1)")
        self.assertEqual([(r[2], r[3]) for r in rows], [(200, 20), (200, 19)], "whole rings on the A-23; X's from the X ring")
        self.assertEqual(res["tie_rules"], "NRA-SB")

    def test_procedures_cite_the_smallbore_book(self):
        t = rb.texts(rb.SB)
        self.assertIn("NRA SB 10.10.5 c", t["move"])
        self.assertIn("1 point", t["crossfire"])
        self.assertEqual(rb.interruption_time(rb.SB, "interrupt", 60_000, 150), (120_000, True, "NRA SB 10.3.2"), "2 minutes or more: +1 minute")
        self.assertEqual(rb.interruption_time(rb.SB, "interrupt", 60_000, 90), (60_000, False, "NRA SB 10.3.2"))
        self.assertEqual(rb.interruption_time(rb.SB, "malfunction", 60_000, 999), (60_000, True, "NRA SB 9.5 a"), "the time that remained")
        self.assertEqual(rb.range_interruption(rb.SB, 130), (60, True, "NRA SB 10.3.2"))
        self.assertEqual(rb.finals(rb.SB), (), "no finals in the smallbore book")


if __name__ == "__main__":
    unittest.main()
