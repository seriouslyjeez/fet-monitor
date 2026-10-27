"""Deleting matches and results independently: results deleted = off the Results page (match kept, can come back);
match deleted = a frozen copy of its results stays on the Results page; shots stay in the targets' history."""
import unittest

from fetserver.matches import MatchError
from tests.test_competitions import BASE, CompCase


class ResultStoreCase(CompCase):
    def setUp(self):
        super().setUp()
        self.rs = self.core.results

    def later(self, s):
        self.at((self.clock.t - BASE) / 1000 + s)

    def keys(self, hidden_too=False):
        return [r["key"] for r in self.rs.listing(hidden_too)]

    def finished_competition(self, name="League"):
        cid = self.cc.create(name, self.fid, "md")
        a, b = self.person(cid, "Ames"), self.person(cid, "Bell")
        rid = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}, {"device_id": "lane-2", "competitor_id": b}], "md")
        self.m.start(rid, "md")
        for x in ("0.30", "1.50"):
            self.later(10)
            self.shoot("lane-1", x)
            self.shoot("lane-2", "3.00")
        self.m.command(rid, "next", "md")
        return cid, rid

    def single(self, start=True):
        mid = self.m.create("Practice night", self.fid, [{"device_id": "lane-3", "competitor": "Cole"}, {"device_id": "lane-4"}], "md")
        if start:
            self.m.start(mid, "md")
            self.later(10)
            self.shoot("lane-3", "0.00")
            self.shoot("lane-4", "2.00")
        return mid


class SingleMatches(ResultStoreCase):
    def test_a_single_match_has_results_like_a_competition(self):
        self.core.handle_heartbeat({"device_id": "lane-4", "boot_count": 1, "uptime_ms": 0, "name": "Lane Four"})
        mid = self.single(start=False)
        self.assertNotIn(f"m{mid}", self.keys(), "not started: nothing to show yet")
        self.m.start(mid, "md")
        self.at(10); self.shoot("lane-3", "0.00"); self.shoot("lane-4", "2.00")
        res = self.rs.get(f"m{mid}")
        rows = res["classes"][0]["individuals"]
        self.assertEqual([(e["name"], e["place"], e["lane"]) for e in rows], [("Cole", 1, 1), ("Lane Four", 2, 2)], "no name: the target's")
        self.assertEqual((res["key"], res["kind"], res["classes"][0]["teams"]), (f"m{mid}", "match", []))
        self.assertIn(f"m{mid}", self.keys())

    def test_keys(self):
        cid, _ = self.finished_competition()
        self.assertEqual(self.rs.get(str(cid))["key"], f"c{cid}", "a plain number is a competition (older links)")
        for bad in ("x1", "c", "", None, "c1234567890", 1.5):
            with self.assertRaises(MatchError, msg=bad):
                self.rs.get(bad)
        rid = self.cc.detail(cid)["relays"][0]["match_id"]
        with self.assertRaises(MatchError):
            self.rs.get(f"m{rid}")                  # a relay is part of its competition's results


class DeleteResults(ResultStoreCase):
    def test_results_go_the_match_stays_and_can_come_back(self):
        cid, rid = self.finished_competition()
        mid = self.single()
        before = self.rs.get(f"c{cid}")
        self.assertEqual(self.rs.delete_results([f"c{cid}", f"m{mid}"], "md"), 2)
        self.assertEqual(self.keys(), [])
        for k in (f"c{cid}", f"m{mid}"):
            with self.assertRaises(MatchError):
                self.rs.get(k)
        self.assertEqual(self.core.matches.board([mid]), [], "not on the TV scoreboard either")
        self.assertNotIn(mid, [b["id"] for b in self.core.matches.board()])
        self.assertEqual(self.cc.detail(cid)["competitors"][0]["family"], "Ames", "the competition is still in Match Control")
        self.assertEqual(self.m.detail(mid)["state"], "running", "and so is the match")
        self.assertEqual(sorted(self.keys(hidden_too=True)), sorted([f"c{cid}", f"m{mid}"]))
        self.assertTrue(all(r["hidden"] for r in self.rs.listing(hidden_too=True)))
        self.rs.restore_results([f"c{cid}"], "md")
        self.assertEqual(self.rs.get(f"c{cid}")["classes"], before["classes"], "shown again, unchanged")
        self.assertEqual(self.keys(), [f"c{cid}"])

    def test_all_or_nothing_and_bad_input(self):
        cid, _ = self.finished_competition()
        with self.assertRaises(MatchError):
            self.rs.delete_results([f"c{cid}", "c999"], "md")
        self.assertEqual(self.keys(), [f"c{cid}"], "nothing deleted")
        for bad in ([], None, "c1", ["zz"], [f"c{cid}"] * 201):
            with self.assertRaises(MatchError, msg=bad):
                self.rs.delete_results(bad, "md")


class DeleteMatches(ResultStoreCase):
    def test_a_frozen_copy_stays_and_the_shots_stay_on_the_targets(self):
        cid, rid = self.finished_competition()
        mid = self.single()
        self.m.command(mid, "abort", "md")
        live_c = self.rs.get(f"c{cid}", with_shots=True)
        live_m = self.rs.get(f"m{mid}")
        out = self.rs.delete_matches([mid], [cid], "md")
        self.assertEqual(out, {"deleted": 2, "frozen": 2})
        self.assertEqual(self.cc.list(), [])
        self.assertEqual(self.m.list(), [])
        for table in ("competitors", "match_lanes", "match_log", "shot_decisions", "matches"):
            self.assertEqual(self.core.db.conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        frozen = [r for r in self.rs.listing() if r["kind"] == "frozen"]
        self.assertEqual(sorted(r["name"] for r in frozen), ["League", "Practice night"])
        fc = self.rs.get(next(r["key"] for r in frozen if r["name"] == "League"), with_shots=True)
        self.assertEqual(fc["classes"], live_c["classes"], "the frozen copy is the results as they were, shots included")
        self.assertEqual((fc["kind"], fc["source"]), ("frozen", "competition"))
        self.assertEqual(next(r["at_ms"] for r in frozen if r["name"] == "League"), BASE, "listed under the match's date")
        fm = self.rs.get(next(r["key"] for r in frozen if r["name"] == "Practice night"))
        self.assertEqual([e["total"] for e in fm["classes"][0]["individuals"]], [e["total"] for e in live_m["classes"][0]["individuals"]])
        self.assertNotIn("shot_list", fm["classes"][0]["individuals"][0], "shots only when asked for")
        self.assertEqual(len(self.core.series_view(self.core.list_series("lane-1")[0]["id"], "scored", None, 15)["bull_shots"]), 2,
                         "the shots stay in the target's own history")
        key = frozen[0]["key"]
        with self.assertRaises(MatchError):
            self.rs.restore_results([key], "md")
        self.rs.delete_results([key], "md")
        self.assertNotIn(key, self.keys(hidden_too=True), "a frozen copy is deleted for good")

    def test_no_copy_when_the_results_were_deleted_or_it_never_started(self):
        cid, _ = self.finished_competition()
        self.rs.delete_results([f"c{cid}"], "md")
        ready = self.m.create("Not yet", self.fid, [{"device_id": "lane-5"}], "md")
        self.assertEqual(self.rs.delete_matches([ready], [cid], "md"), {"deleted": 2, "frozen": 0})
        self.assertEqual(self.keys(hidden_too=True), [])
        self.m.create("Again", self.fid, [{"device_id": "lane-5"}], "md")     # its target is free again

    def test_running_matches_and_relays_are_refused_all_or_nothing(self):
        cid, rid = self.finished_competition()
        mid = self.single()                            # still running
        with self.assertRaises(MatchError) as ctx:
            self.rs.delete_matches([mid], [cid], "md")
        self.assertIn("stop it", str(ctx.exception))
        self.assertEqual(len(self.cc.list()), 1, "nothing deleted")
        self.assertEqual(self.keys(), [f"m{mid}", f"c{cid}"])
        with self.assertRaises(MatchError) as ctx:
            self.rs.delete_matches([rid], [], "md")
        self.assertIn("relay", str(ctx.exception))
        ready = self.m.create("Not yet", self.fid, [{"device_id": "lane-6"}], "md")
        for bad in (([], []), (None, None), ([str(ready)], []), ([], [str(cid)]), ([999], [])):
            with self.assertRaises(MatchError, msg=bad):
                self.rs.delete_matches(*bad, "md")
        self.assertEqual(len(self.m.list()), 3, "nothing was deleted")


if __name__ == "__main__":
    unittest.main()
