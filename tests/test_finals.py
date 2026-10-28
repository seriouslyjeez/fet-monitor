"""CMP Standard Final (10.4): the top qualifiers fire 10 single shots on command (LOAD, 5 s, START, 50 s or all
fired), added to their qualification score; ties for 1st-3rd are shot off (10.2.9 n); "Final for All Athletes"
(10.1.2 a) after each relay, where only the class's top 8 qualifiers count."""
from fetserver.matches import MatchError
from tests.test_matches import MatchCase


class FinalCase(MatchCase):
    def setUp(self):
        super().setUp()
        for d in ("lane-3", "lane-4", "lane-5"):
            self.core.handle_heartbeat({"device_id": d, "boot_count": 1, "uptime_ms": 0})
        self.cc = self.core.competitions
        fid = self.m.save_format({"name": "Q", "tie_rules": "CMP", "phases": [{"kind": "record", "label": "R", "seconds": 100, "shots": 2}]}, "md")["id"]
        self.cid = self.cc.create("League", fid, "md")
        self.ids = [self.cc.save_competitor(self.cid, {"family": f}) for f in ("Ames", "Bell", "Cole", "Dunn")]
        self.r1 = self.cc.add_relay(self.cid, [{"device_id": f"lane-{i + 1}", "competitor_id": p} for i, p in enumerate(self.ids)], "md")
        self.m.start(self.r1, "md")
        self.t = 10
        for lane, x in (("lane-1", "0.20"), ("lane-2", "0.20"), ("lane-3", "1.50"), ("lane-4", "3.00")):   # Ames = Bell > Cole > Dunn
            self.at(self.t); self.shoot(lane, x); self.shoot(lane, x)
        self.at(200)                                                  # relay 1 done

    def run_shot(self, mid, shots):
        """Go through holds until a shot phase runs, fire `shots` {device: x}; the phase ends when all have fired."""
        for _ in range(10):
            d = self.m.detail(mid)
            if d["state"] == "holding":
                self.m.command(mid, "go", "ro")
            elif d["clock"]["kind"] == "record":
                break
            else:
                self.clock.t += d["clock"]["remaining_ms"]; self.m.tick()
        for did, x in shots.items():
            self.shoot(did, x)
        self.m.tick()

    def final(self, devices=("lane-5", "lane-4", "lane-3"), **kw):
        return self.cc.create_final(self.cid, "standard", list(devices), "md", **kw)


class StandardFinal(FinalCase):
    def test_finalists_points_and_ranking(self):
        mid = self.final(n=3)
        lanes = self.m.detail(mid)["lanes"]
        self.assertEqual([(l["target"], l["competitor"]) for l in lanes], [("lane-5", "AMES"), ("lane-4", "BELL"), ("lane-3", "COLE")],
                         "best qualifier on point A (10.4.1); ties by countback")
        self.assertEqual(self.m.detail(mid)["clock"]["paced"], "officer")
        self.m.start(mid, "md")
        for _ in range(3):                                         # positions, sighting, the pause: each held for Next
            self.m.command(mid, "go", "ro"); self.clock.t += self.m.detail(mid)["clock"]["remaining_ms"]; self.m.tick()
        self.assertEqual((self.m.detail(mid)["state"], self.m.detail(mid)["clock"]["label"]), ("holding", "Shot 1: load"))
        self.m.command(mid, "go", "ro")                            # LOAD ...
        self.clock.t += 5000; self.m.tick()
        self.assertEqual((self.m.detail(mid)["state"], self.m.detail(mid)["clock"]["label"]), ("running", "Shot 1"), "... START follows by itself")
        for k in range(10):
            self.run_shot(mid, {"lane-5": "0.50", "lane-4": "0.50", "lane-3": "1.50"})
            if k == 0:
                self.assertEqual(self.m.detail(mid)["clock"]["label"], "Shot 2: load", "STOP as soon as all have fired (10.4.6 c)")
        self.assertEqual(self.m.detail(mid)["state"], "done")
        f = self.cc.results(self.cid)["finals"][0]
        rows = {r["name"]: r for r in f["rows"]}
        self.assertEqual(rows["COLE"]["shots"], 10)
        self.assertAlmostEqual(rows["COLE"]["total"], rows["COLE"]["qual"] + rows["COLE"]["final"])
        self.assertEqual((rows["AMES"]["place"], rows["BELL"]["place"], rows["AMES"]["tied"]), (1, 1, True), "a medal tie waits for a shoot-off")
        self.assertEqual(f["shoot_off_needed"], [[1, 2]])
        quals = {e["name"]: e["shots"] for cl in self.cc.results(self.cid)["classes"] for e in cl["individuals"]}
        self.assertEqual(quals["AMES"], 2, "the final does not count in the qualification")
        # the shoot-off: firing points 1 and 2 only; COLE's shot is flagged
        self.m.shoot_off(mid, [1, 2], "md")
        self.run_shot(mid, {})
        self.assertEqual(self.mode("lane-3"), "sighter", "not in the shoot-off: its target stays in sighting")
        self.run_shot(mid, {"lane-5": "0.10", "lane-4": "0.90"})
        self.assertEqual(self.m.detail(mid)["state"], "done", "the shoot-off shot ends when the two tied have fired")
        self.shoot("lane-3", "0.00")
        f = self.cc.results(self.cid)["finals"][0]
        rows = {r["name"]: r for r in f["rows"]}
        self.assertEqual((rows["AMES"]["place"], rows["BELL"]["place"], f["shoot_off_needed"]), (1, 2, []))
        self.assertEqual(rows["AMES"]["final"], rows["BELL"]["final"], "the shoot-off shot is not part of the final score")
        self.assertIn("not in it", [x[3] for x in self.flags(mid) if x[0] == "lane-3"][0])
        with self.assertRaises(MatchError):
            self.m.shoot_off(mid, [3], "md")                     # one firing point is not a tie

    def test_errors_and_final_for_all(self):
        for bad in ({"devices": ["lane-5"], "n": 2}, {"devices": [], "n": None}, {"devices": ["lane-5", "lane-5"], "n": 1}):
            with self.assertRaises(MatchError, msg=bad):
                self.cc.create_final(self.cid, "standard", bad["devices"], "md", n=bad["n"])
        with self.assertRaises(MatchError):
            self.final(cls="Junior")
        with self.assertRaises(MatchError):
            self.cc.create_final(self.cid, "sfz_standing", ["lane-5"], "md")
        r2 = self.cc.add_relay(self.cid, [{"device_id": "lane-5", "competitor_id": self.cc.save_competitor(self.cid, {"family": "Eve"})}], "md")
        with self.assertRaises(MatchError) as ctx:
            self.cc.create_final(self.cid, "standard", None, "md", for_relay=r2)   # not finished
        self.assertIn("after that relay has finished", str(ctx.exception))
        mid = self.cc.create_final(self.cid, "standard", None, "md", for_relay=self.r1)
        self.assertEqual([l["target"] for l in self.m.detail(mid)["lanes"]], ["lane-1", "lane-2", "lane-3", "lane-4"], "everyone, on their own target")
        with self.assertRaises(MatchError):
            self.cc.create_final(self.cid, "standard", None, "md", for_relay=self.r1)
        self.m.start(mid, "md")
        for _ in range(10):
            self.run_shot(mid, {"lane-1": "0.50", "lane-2": "1.00", "lane-3": "1.50", "lane-4": "3.00"})
        finals = self.cc.results(self.cid)["finals"]
        self.assertEqual([f["kind"] for f in finals], ["standard", "standard_all"])
        pooled = finals[1]["rows"]
        self.assertEqual([r["name"] for r in pooled][:2], ["AMES", "BELL"], "Ames and Bell tied after qualification: the final decides")
        self.assertEqual(self.cc.detail(self.cid)["relays"][0]["relay"], 1)
        self.assertEqual([r["relay"] for r in self.cc.detail(self.cid)["relays"]], [1, 2], "a final is not a relay")


class RankingRules(FinalCase):
    def test_ties_below_the_medals_go_by_the_final_score(self):
        from fetserver.ranking import summary
        def row(name, total, final, so=()):
            return {"name": name, "total": total, "final": final, "shoot_off": list(so), "dq": None,
                    "summary": summary([{"tenths": int(final * 10), "ring": int(final), "inner": False}])}
        rows = self.cc._rank_final([row("A", 300, 100), row("B", 299, 99), row("C", 299, 98), row("D", 299, 97), row("E", 298, 96),
                                    row("F", 298, 95)])
        self.assertEqual([(r["name"], r["place"], r["tied"]) for r in rows],
                         [("A", 1, False), ("B", 2, True), ("C", 2, True), ("D", 2, True), ("E", 5, False), ("F", 6, False)],
                         "B-D tied into the medals: shoot-off; E/F (places 5-6): the final score")
        pooled = self.cc._rank_final([row("B", 299, 99), row("C", 299, 98)], shoot_offs=False)
        self.assertEqual([(r["name"], r["place"]) for r in pooled], [("B", 1), ("C", 2)], "no shoot-off possible: the final score")


class FinalForAllTopEight(FinalCase):
    def test_only_the_top_eight_count(self):
        for i in range(6, 11):
            self.core.handle_heartbeat({"device_id": f"lane-{i}", "boot_count": 1, "uptime_ms": 0})
        more = [self.cc.save_competitor(self.cid, {"family": f"Z{i}"}) for i in range(5)]       # 9 competitors in all
        r2 = self.cc.add_relay(self.cid, [{"device_id": f"lane-{i + 6}", "competitor_id": p} for i, p in enumerate(more)], "md")
        self.m.start(r2, "md")                                    # its 100 s record stage runs from 200 to 300
        self.at(250)
        for i, p in enumerate(more):
            self.shoot(f"lane-{i + 6}", "4.00" if i == 4 else "2.50"); self.shoot(f"lane-{i + 6}", "4.00" if i == 4 else "2.50")
        self.at(500)
        f2 = self.cc.create_final(self.cid, "standard", None, "md", for_relay=r2)
        self.m.start(f2, "md")
        for _ in range(10):
            self.run_shot(f2, {f"lane-{i + 6}": "0.00" for i in range(5)})
        res = self.cc.results(self.cid)
        relay_final = next(f for f in res["finals"] if f["match_id"] == f2)
        last = next(r for r in relay_final["rows"] if r["name"] == "Z4")
        self.assertFalse(last["counts"], "9th in the qualification: for information only")
        pooled = next(f for f in res["finals"] if f["kind"] == "standard_all")
        self.assertNotIn("Z4", [r["name"] for r in pooled["rows"]])
        self.assertEqual([r["relay"] for r in self.cc.list()[0]["relays"]], [1, 2], "a final is not listed as a relay")
