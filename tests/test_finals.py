"""CMP Standard Final (10.4): the top qualifiers fire 10 single shots on command (LOAD, 5 s, START, 50 s or all
fired), added to their qualification score; ties for 1st-3rd are shot off (10.2.9 n); "Final for All Athletes"
(10.1.2 a) after each relay, where only the class's top 8 qualifiers count."""
from fetserver.matches import MatchError
from tests.test_matches import MatchCase


class FinalCase(MatchCase):
    BOOK = "CMP-3PAR"                 # the qualification's rulebook: its finals only

    def setUp(self):
        super().setUp()
        for d in ("lane-3", "lane-4", "lane-5"):
            self.core.handle_heartbeat({"device_id": d, "boot_count": 1, "uptime_ms": 0})
        self.cc = self.core.competitions
        fid = self.m.save_format({"name": "Q", "book": self.BOOK, "phases": [{"kind": "record", "label": "R", "seconds": 100, "shots": 2}]}, "md")["id"]
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


class SpokenFinal(FinalCase):
    def test_spoken_final_runs_by_itself_with_time_for_scores(self):
        mid = self.final(n=2, devices=("lane-5", "lane-4"), paced="spoken")
        d = self.m.detail(mid)
        self.assertEqual(d["clock"]["paced"], "spoken")
        labels = [p["label"] for p in d["format"]["phases"]]
        self.assertEqual(labels[3:8], ["Shot 1: load", "Shot 1", "Scores after shot 1", "Shot 2: load", "Shot 2"])
        self.assertEqual(sum(1 for x in labels if x.startswith("Scores after")), 9)
        self.m.start(mid, "md")
        self.assertEqual(self.m.detail(mid)["state"], "running", "no holds")
        with self.assertRaises(MatchError):
            self.final(n=1, devices=("lane-3",), paced="loud")
        self.assertEqual(sum(1 for p in self.m.detail(self.final(n=1, devices=("lane-3",)))["format"]["phases"] if p["label"].startswith("Scores")), 0,
                         "officer-paced (default): the range officer takes the time")


class AirGunFinal(FinalCase):
    BOOK = "CMP-AG"

    """CMP Air Gun Final (Air Rifle and Air Pistol Rules 2026, 10.2): draw, 2 x 5 shots, 14 single shots, the
    lowest out after shots 12..22 (only places that exist), shot 24 decides 1st/2nd, every tie shot off (10.3.5)."""
    def drive(self, mid, x_of, until=None):
        """Run the final: Next at every hold; in a shot phase each finalist still in it fires (x_of(device, shot no.)
        gives the offset); time runs out for series and single shots (no early STOP), shoot-offs end when all fired."""
        shot = 0
        for _ in range(400):
            d = self.m.detail(mid)
            if d["state"] == "done" or (until and until(d)):
                return d
            if d["state"] == "holding":
                self.m.command(mid, "go", "ro"); continue
            ph = d["format"]["phases"][d["clock"]["phase"]]
            if ph["kind"] == "record":
                lanes = {l["lane"]: l for l in d["lanes"]}
                if ph.get("lanes") is None:
                    shot += ph["shots"]
                for l in lanes.values():
                    if l.get("out") is None and (ph.get("lanes") is None or l["lane"] in ph["lanes"]) and not self.is_out(mid, l["lane"]):
                        for _ in range(ph["shots"]):
                            self.shoot(l["target"], x_of(l["target"], shot, ph))
                self.m.tick()
                if self.m.detail(mid)["clock"]["phase"] != d["clock"]["phase"] or self.m.detail(mid)["state"] == "done":
                    continue
            self.clock.t += max(1, self.m.detail(mid)["clock"]["remaining_ms"]); self.m.tick()
        self.fail("the final did not finish")

    def is_out(self, mid, lane):
        r = self.core.db.conn().execute("SELECT out_place FROM match_lanes WHERE match_id=? AND lane=?", (mid, lane)).fetchone()
        return r["out_place"] is not None

    def places(self, mid):
        return {r["device_id"]: r["out_place"] for r in self.core.db.conn().execute("SELECT * FROM match_lanes WHERE match_id=?", (mid,))}

    def test_draw_eliminations_and_places(self):
        mid = self.cc.create_final(self.cid, "airgun", ["lane-5", "lane-4", "lane-3", "lane-2"], "md")
        d = self.m.detail(mid)
        self.assertEqual(sorted(l["competitor"] for l in d["lanes"]), ["AMES", "BELL", "COLE", "DUNN"], "the top 4, in a random draw")
        self.assertEqual([l["pos"] for l in d["lanes"]], ["A", "B", "C", "D"])
        self.cc.redraw_final(mid, "md")
        value = {"lane-5": "0.10", "lane-4": "0.60", "lane-3": "1.20", "lane-2": "2.00"}    # A best ... D worst
        self.m.start(mid, "md")
        with self.assertRaises(MatchError):
            self.cc.redraw_final(mid, "md")
        d = self.drive(mid, lambda dev, n, ph: value[dev], until=lambda d: d["clock"]["label"] == "Shot 21: load")
        self.assertEqual(self.places(mid), {"lane-5": None, "lane-4": None, "lane-3": None, "lane-2": 4},
                         "with 4 finalists the first eliminations are skipped; after shot 20 the 4th is out")
        self.assertEqual(self.mode("lane-2"), "sighter")
        d = self.drive(mid, lambda dev, n, ph: value[dev], until=lambda d: d["clock"]["label"] == "Shot 21")
        self.assertEqual((self.mode("lane-5"), self.mode("lane-2")), ("record", "sighter"), "out: never switched to record again")
        self.shoot("lane-2", "0.00")                               # after being eliminated: flagged
        d = self.drive(mid, lambda dev, n, ph: value[dev])
        self.assertEqual(self.places(mid), {"lane-5": 1, "lane-4": 2, "lane-3": 3, "lane-2": 4})
        self.assertIn("after being eliminated", [f[3] for f in self.flags(mid) if f[0] == "lane-2"])
        f = next(x for x in self.cc.results(self.cid)["finals"] if x["match_id"] == mid)
        self.assertEqual([(r["device_id"], r["place"]) for r in f["rows"]], [("lane-5", 1), ("lane-4", 2), ("lane-3", 3), ("lane-2", 4)])
        self.assertEqual(f["rows"][0]["shots"], 24)
        self.assertEqual(f["rows"][0]["total"], f["rows"][0]["final"], "the final alone decides")

    def test_a_tie_is_shot_off_at_once(self):
        mid = self.cc.create_final(self.cid, "airgun", ["lane-5", "lane-4", "lane-3"], "md", n=3)
        devs = [l["target"] for l in self.m.detail(mid)["lanes"]]
        self.m.start(mid, "md")
        # two finalists equal all the way: at shot 22 (3rd place) they tie -> shoot-off; then the third one wins
        tied = devs[:2]
        def x(dev, n, ph):
            if ph.get("lanes"):                                     # the shoot-off: the first tied one wins it
                return "0.00" if dev == tied[0] else "1.50"
            return "1.00" if dev in tied else "0.00"
        d = self.drive(mid, x, until=lambda d: d["clock"]["label"].startswith("Shoot-off 1"))
        so = next(p for p in d["format"]["phases"] if p["label"] == "Shoot-off 1")
        self.assertEqual((so["lanes"], so.get("round") is not None), (sorted(l["lane"] for l in d["lanes"] if l["target"] in tied), True))
        self.drive(mid, x)
        self.assertEqual(self.places(mid), {devs[2]: 1, tied[0]: 2, tied[1]: 3}, "the shoot-off decided 3rd")

    def test_fewer_than_eight_and_spoken(self):
        with self.assertRaises(MatchError):
            self.cc.create_final(self.cid, "airgun", None, "md", for_relay=self.r1)
        mid = self.cc.create_final(self.cid, "airgun", ["lane-5", "lane-4"], "md", paced="spoken")
        labels = [p["label"] for p in self.m.detail(mid)["format"]["phases"]]
        self.assertEqual(labels.count("Scores and commentary"), 15)
        self.m.start(mid, "md")
        d = self.drive(mid, lambda dev, n, ph: "0.10" if dev == "lane-5" else "2.00")
        self.assertEqual(self.places(mid), {"lane-5": 1, "lane-4": 2}, "two finalists: only shot 24 decides")

    def test_shoot_offs_that_never_break_do_not_jam_the_clock(self):
        mid = self.cc.create_final(self.cid, "airgun", ["lane-5", "lane-4"], "md")
        self.m.start(mid, "md")
        d = self.drive(mid, lambda dev, n, ph: "1.00")               # always equal
        self.assertEqual(d["state"], "done")
        self.assertEqual(self.places(mid), {"lane-5": None, "lane-4": None}, "undecided: for the match director")
        self.assertEqual(self.core.db.conn().execute("SELECT COUNT(*) FROM match_log WHERE match_id=? AND kind='shoot_off_limit'", (mid,)).fetchone()[0], 1)


class Draw(FinalCase):
    BOOK = "CMP-AG"

    def test_the_draw_shuffles(self):
        import random
        orig = random.SystemRandom.shuffle
        random.SystemRandom.shuffle = lambda self, x: x.reverse()
        try:
            mid = self.cc.create_final(self.cid, "airgun", ["lane-5", "lane-4", "lane-3"], "md", n=3)
        finally:
            random.SystemRandom.shuffle = orig
        self.assertEqual([l["competitor"] for l in self.m.detail(mid)["lanes"]], ["COLE", "BELL", "AMES"],
                         "the draw (here: reversed) decides the firing points, not the ranking")
