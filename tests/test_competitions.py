"""Competitions: competitors, relays (each its own match), results per class across relays, and teams."""
import os
import tempfile
import unittest

from fetserver.core import Core
from fetserver.db import Database
from fetserver.matches import MatchError

BASE = 1_000_000_000
SHOT = '{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":0.00 , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'


class Clock:
    def __init__(self):
        self.t = BASE

    def __call__(self):
        return self.t


class CompCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.core = Core(Database(os.path.join(tmp.name, "t.db")), now_ms=self.clock)
        self.cc, self.m = self.core.competitions, self.core.matches
        self.seq = {}
        for i in range(1, 7):
            self.core.handle_heartbeat({"device_id": f"lane-{i}", "boot_count": 1, "uptime_ms": 0})
        self.fid = self.m.save_format({"name": "Two", "book": "ISSF", "scoring": "decimal", "phases": [{"kind": "record", "label": "R", "seconds": 600, "shots": 2}]}, "md")["id"]

    def at(self, s):
        self.clock.t = BASE + int(s * 1000)
        self.m.tick()

    def shoot(self, did, x):
        n = self.seq[did] = self.seq.get(did, 0) + 1
        self.core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": self.clock.t - BASE, "events": [
            {"seq": n, "type": "shot", "uptime_ms": self.clock.t - BASE, "raw": SHOT % (n, x)}]})

    def person(self, cid, family, given=None, cls=None, team=None):
        return self.cc.save_competitor(cid, {"family": family, "given": given, "class": cls, "team": team})


class Basics(CompCase):
    def test_team_size_defaults_to_the_rulebook(self):
        self.assertEqual(self.cc.detail(self.cc.create("A", "issf-ar60", "md"))["team_size"], 3)
        self.assertEqual(self.cc.detail(self.cc.create("B", "cmp-3x20", "md"))["team_size"], 4)
        d = self.cc.detail(self.cc.create("C", "nra-3x20", "md", team_size=5, best_count=True))
        self.assertEqual((d["team_size"], d["best_count"], d["tie_rules"], d["paced"]), (5, True, "NRA", "spoken"))
        for bad in ({"team_size": 0}, {"team_size": 11}, {"best_count": "yes"}, {"paced": "loud"}):
            with self.assertRaises(MatchError):
                self.cc.create("X", "issf-ar60", "md", **bad)
        with self.assertRaises(MatchError):
            self.cc.create("", "issf-ar60", "md")

    def test_competitors(self):
        cid = self.cc.create("A", self.fid, "md")
        a = self.person(cid, "  Smith ", "Ann", "Junior", "Club A")
        self.person(cid, "adams")
        with self.assertRaises(MatchError):
            self.person(cid, "  ")
        names = [p["name"] for p in self.cc.detail(cid)["competitors"]]
        self.assertEqual(names, ["ADAMS", "SMITH Ann"], "FAMILY Given, alphabetical")
        self.cc.save_competitor(cid, {"family": "Smith", "given": "Anne"}, a)
        self.assertEqual(self.cc.detail(cid)["competitors"][1]["name"], "SMITH Anne")
        self.cc.remove_competitor(cid, a)
        self.assertEqual(len(self.cc.detail(cid)["competitors"]), 1)


class Relays(CompCase):
    def test_relays_and_who_can_be_where(self):
        cid = self.cc.create("League", self.fid, "md", paced="officer")
        a, b, c = (self.person(cid, n) for n in ("Ames", "Brown", "Cole"))
        r1 = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}, {"device_id": "lane-2", "competitor_id": b}], "md")
        d = self.m.detail(r1)
        self.assertEqual((d["name"], d["clock"]["paced"], [l["competitor"] for l in d["lanes"]]), ("League - relay 1", "officer", ["AMES", "BROWN"]))
        with self.assertRaises(MatchError):
            self.cc.add_relay(cid, [{"device_id": "lane-3", "competitor_id": a}], "md")          # already on relay 1
        with self.assertRaises(MatchError):
            self.cc.add_relay(cid, [{"device_id": "lane-3", "competitor_id": 999}], "md")
        with self.assertRaises(MatchError):
            self.cc.remove_competitor(cid, a)                                                      # on a relay
        r2 = self.cc.add_relay(cid, [{"device_id": "lane-3", "competitor_id": c}], "md")
        self.assertEqual(self.m.detail(r2)["name"], "League - relay 2")
        self.m.start(r2, "ro")
        self.m.command(r2, "abort", "ro")
        r3 = self.cc.add_relay(cid, [{"device_id": "lane-3", "competitor_id": c}], "md")          # a stopped relay frees them
        self.assertEqual(self.m.detail(r3)["name"], "League - relay 3")
        self.cc.save_competitor(cid, {"family": "Cole", "given": "Cat"}, c)
        self.assertEqual(self.m.detail(r3)["lanes"][0]["competitor"], "COLE Cat", "a renamed competitor shows on the screens")
        relays = self.cc.detail(cid)["relays"]
        self.assertEqual([(x["relay"], x["state"]) for x in relays], [(1, "ready"), (2, "aborted"), (3, "ready")])


class Results(CompCase):
    def test_classes_across_relays_and_teams(self):
        cid = self.cc.create("League", self.fid, "md", team_size=2)
        p = {n: self.person(cid, n, cls=cl, team=t) for n, cl, t in (
            ("Ames", "Open", "Red"), ("Brown", "Open", "Red"), ("Cole", "Open", "Blue"), ("Dunn", "Junior", "Blue"), ("Eck", "Open", None))}
        r1 = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": p["Ames"]}, {"device_id": "lane-2", "competitor_id": p["Brown"]},
                                     {"device_id": "lane-3", "competitor_id": p["Dunn"]}], "md")
        r2 = self.cc.add_relay(cid, [{"device_id": "lane-4", "competitor_id": p["Cole"]}, {"device_id": "lane-5", "competitor_id": p["Eck"]}], "md")
        for mid in (r1, r2):
            self.m.start(mid, "ro")
        self.at(5)
        for did, x in (("lane-1", "0.30"), ("lane-2", "1.00"), ("lane-3", "0.00"), ("lane-4", "0.60"), ("lane-5", "5.00")):
            self.shoot(did, x); self.shoot(did, x)
        res = self.cc.results(cid)
        self.assertEqual([c["name"] for c in res["classes"]], ["Junior", "Open"])
        open_ = res["classes"][1]
        self.assertEqual([(e["name"], e["place"], e["relay"]) for e in open_["individuals"]],
                         [("AMES", 1, 1), ("COLE", 2, 2), ("BROWN", 3, 1), ("ECK", 4, 2)], "one list across both relays")
        teams = {t["name"]: t for t in open_["teams"]}
        self.assertEqual((teams["Red"]["place"], teams["Red"]["counting"]), (1, ["AMES", "BROWN"]))
        self.assertIsNone(teams["Blue"]["place"], "Blue has only one Open member")
        self.assertIn("1 of 2", teams["Blue"]["reason"])
        self.assertAlmostEqual(teams["Red"]["total"], open_["individuals"][0]["total"] + open_["individuals"][2]["total"], places=5)

    def test_best_count_teams(self):
        cid = self.cc.create("T", self.fid, "md", team_size=2)
        ids = [self.person(cid, n, team="Club") for n in ("A", "B", "C")]
        rid = self.cc.add_relay(cid, [{"device_id": f"lane-{i + 1}", "competitor_id": x} for i, x in enumerate(ids)], "md")
        self.m.start(rid, "ro")
        self.at(5)
        for did, x in (("lane-1", "4.00"), ("lane-2", "0.00"), ("lane-3", "1.00")):
            self.shoot(did, x); self.shoot(did, x)
        t = self.cc.results(cid)["classes"][0]["teams"][0]
        self.assertEqual((t["place"], t["reason"] if "reason" in t else None), (None, "3 members for a team of 2"))
        self.cc.update(cid, best_count=True)
        t = self.cc.results(cid)["classes"][0]["teams"][0]
        self.assertEqual((t["place"], t["counting"]), (1, ["B", "C"]), "the best two count")

    def test_no_shots_yet_and_aborted_relays(self):
        cid = self.cc.create("T", self.fid, "md")
        a = self.person(cid, "Ames")
        rid = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}], "md")
        e = self.cc.results(cid)["classes"][0]["individuals"][0]
        self.assertEqual((e["shots"], e["total"], e["fired"], e["relay"]), (0, 0, False, 1))
        self.m.start(rid, "ro"); self.at(5); self.shoot("lane-1", "0.00"); self.m.command(rid, "abort", "ro")
        e = self.cc.results(cid)["classes"][0]["individuals"][0]
        self.assertEqual((e["shots"], e["relay"]), (0, None), "an aborted relay does not count")
        with_shots = self.cc.results(cid, with_shots=True)["classes"][0]["individuals"][0]
        self.assertEqual(with_shots["shot_list"], [])



class IntegerPenalty(CompCase):
    def test_two_points_off_in_whole_rings(self):
        fid = self.m.save_format({"name": "Int", "book": "CMP-3PAR", "scoring": "integer", "phases": [
            {"kind": "setup", "label": "S", "seconds": 10}, {"kind": "record", "label": "R", "seconds": 600, "shots": 2}]}, "md")["id"]
        cid = self.cc.create("I", fid, "md")
        a = self.person(cid, "Ames")
        rid = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}], "md")
        self.m.start(rid, "ro")
        self.at(20)
        self.shoot("lane-1", "0.00"); self.shoot("lane-1", "0.00"); self.shoot("lane-1", "0.00")     # the 3rd is one too many
        e = self.cc.results(cid)["classes"][0]["individuals"][0]
        self.assertEqual((e["total"], e["shots"]), (20, 2))
        flag = self.m.detail(rid)["flags"][0]
        self.m.decide(rid, flag["shot_id"], "penalty2", "md")
        e = self.cc.results(cid)["classes"][0]["individuals"][0]
        self.assertEqual((e["total"], e["shots"], e["x"]), (28, 3, 2), "10 + 10 + (10 - 2); a penalised shot is no inner ten")


class CsvImport(CompCase):
    def names(self, cid):
        return [(p["family"], p["given"], p["class"], p["team"]) for p in self.cc.detail(cid)["competitors"]]

    def test_formats(self):
        cid = self.cc.create("A", self.fid, "md")
        self.assertEqual(self.cc.import_csv(cid, "Smith,Ann,Open,Red\nJones\n")["added"], 2, "no header: Family, Given, Class, Team")
        self.assertEqual(self.names(cid), [("Jones", None, None, None), ("Smith", "Ann", "Open", "Red")])
        cid2 = self.cc.create("B", self.fid, "md")
        self.cc.import_csv(cid2, "Team;First name;Last name\nBlue;Cy;Cole\n\n")
        self.assertEqual(self.names(cid2), [("Cole", "Cy", None, "Blue")], "header in any order, semicolons, blank lines")
        cid3 = self.cc.create("C", self.fid, "md")
        self.cc.import_csv(cid3, "Surname\tForename\tCategory\nDunn\tDi\tJunior\n")
        self.assertEqual(self.names(cid3), [("Dunn", "Di", "Junior", None)], "tabs and other header words")
        self.cc.import_csv(cid3, '"O\'Neil, Jr.",Pat\n')
        self.assertIn(("O'Neil, Jr.", "Pat", None, None), self.names(cid3), "quoted fields")

    def test_all_or_nothing(self):
        cid = self.cc.create("A", self.fid, "md")
        with self.assertRaises(MatchError) as cm:
            self.cc.import_csv(cid, "Smith,Ann\n,Bob\nJones\n" + "X" * 61 + "\n")
        self.assertIn("line 2: no family name", str(cm.exception))
        self.assertIn("line 4: a field is too long", str(cm.exception))
        self.assertEqual(self.names(cid), [], "nothing added")
        with self.assertRaises(MatchError):
            self.cc.import_csv(cid, "\n \n")


class Rename(CompCase):
    def test_a_new_name_reaches_the_target_screens(self):
        cid = self.cc.create("A", self.fid, "md")
        a = self.person(cid, "Ames", "Al")
        rid = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}], "md")
        self.m.start(rid, "ro")
        self.at(5); self.shoot("lane-1", "0.00")
        self.cc.save_competitor(cid, {"family": "Amos", "given": "Al"}, a)
        t = next(x for x in self.core.list_targets() if x["device_id"] == "lane-1")
        self.assertEqual((t["shooter"], t["match"]["competitor"]), ("AMOS Al", "AMOS Al"))
        self.at(10); self.shoot("lane-1", "0.00")                    # the series carries on under the new name
        self.assertEqual(self.cc.results(cid)["classes"][0]["individuals"][0]["shots"], 2)


class MoveBeforeStart(CompCase):
    def seats(self, cid):
        return {(r["relay"], l["device_id"]): l["competitor"] for r in self.cc.detail(cid)["relays"] for l in r["lanes"]}

    def test_swap_free_target_another_relay_and_off(self):
        cid = self.cc.create("A", self.fid, "md")
        a, b, c = self.person(cid, "Ames"), self.person(cid, "Bell"), self.person(cid, "Cole")
        r1 = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}, {"device_id": "lane-2", "competitor_id": b}], "md")
        r2 = self.cc.add_relay(cid, [{"device_id": "lane-3", "competitor_id": c}], "md")
        self.cc.move_competitor(cid, a, r1, "lane-2", "md")                      # onto Bell: they swap
        self.assertEqual(self.seats(cid), {(1, "lane-1"): "BELL", (1, "lane-2"): "AMES", (2, "lane-3"): "COLE"})
        self.cc.move_competitor(cid, a, r1, "lane-4", "md")                      # a free target in the same relay
        self.assertEqual(self.seats(cid), {(1, "lane-1"): "BELL", (1, "lane-4"): "AMES", (2, "lane-3"): "COLE"})
        self.cc.move_competitor(cid, a, r2, "lane-5", "md")                      # to another relay
        self.assertEqual(self.seats(cid), {(1, "lane-1"): "BELL", (2, "lane-3"): "COLE", (2, "lane-5"): "AMES"})
        self.assertEqual([l["lane"] for l in self.cc.detail(cid)["relays"][1]["lanes"]], [1, 2], "a new firing point at the end")
        self.cc.move_competitor(cid, a, None, None, "md")                        # off the relays
        self.assertEqual(self.seats(cid), {(1, "lane-1"): "BELL", (2, "lane-3"): "COLE"})
        self.cc.move_competitor(cid, a, r1, "lane-6", "md")                      # and back onto one
        self.assertEqual(self.seats(cid)[(1, "lane-6")], "AMES")
        for args, why in (((b, None, None), "last competitor"), ((a, r1, "lane-1"), None), ((a, r1, "lane-3"), "relay 2"),
                          ((a, r1, "lane-99"), "unknown target"), ((a, 999, "lane-5"), "unknown relay")):
            if why is None:                                                      # a swap within the relay is fine
                self.cc.move_competitor(cid, *args, "md")
                continue
            if args[0] == b:
                self.cc.move_competitor(cid, a, r2, "lane-5", "md")             # Bell alone on relay 1
            with self.assertRaises(MatchError, msg=why) as ctx:
                self.cc.move_competitor(cid, *args, "md")
            self.assertIn(why, str(ctx.exception))

    def test_not_after_the_start_and_not_onto_someone_from_nowhere(self):
        cid = self.cc.create("A", self.fid, "md")
        a, b, c = self.person(cid, "Ames"), self.person(cid, "Bell"), self.person(cid, "Cole")
        r1 = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}, {"device_id": "lane-2", "competitor_id": b}], "md")
        with self.assertRaises(MatchError):
            self.cc.move_competitor(cid, c, r1, "lane-1", "md")                 # Cole is on no relay: no swap partner place
        self.m.create("Other", self.fid, [{"device_id": "lane-5"}], "md")
        with self.assertRaises(MatchError) as ctx:
            self.cc.move_competitor(cid, c, r1, "lane-5", "md")                 # a target in another match
        self.assertIn("Other", str(ctx.exception))
        self.m.start(r1, "md")
        for args in ((a, r1, "lane-3"), (a, None, None), (c, r1, "lane-3")):
            with self.assertRaises(MatchError, msg=args) as ctx:
                self.cc.move_competitor(cid, *args, "md")
            self.assertIn("start", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
