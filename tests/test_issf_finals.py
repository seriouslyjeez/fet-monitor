"""ISSF finals beyond 10 m air rifle (ISSF Rule Book 2026): the 50 m Rifle 3 Positions Final (6.17.3: kneeling and prone
on one 22-minute clock with the switch between positions, standing series, eliminations, the countback for two tied
after the second standing series) and the 10 m Air Pistol Final (6.17.2, on the air pistol face)."""
from fetcore import matchformats as mf
from fetserver.matches import MatchError
from tests.test_finals_more import Drive
from tests.test_matches import MatchCase

THREE_P = [{"kind": "record", "label": k.title(), "seconds": 100, "shots": 1, "position": k} for k in ("kneeling", "prone", "standing")]


class Course(MatchCase):
    def test_the_final_as_the_rule_reads(self):
        f = mf.issf_3p_final()
        ph = [(p["kind"], p["label"], p["seconds"]) for p in f["phases"][:7]]
        self.assertEqual(ph, [("setup", "Take your positions", 30), ("sight", "Preparation and sighting, kneeling", 300),
                              ("pause", "Targets reset to match", 30), ("pause", "Match firing: load", 5),
                              ("record", "Kneeling and prone", 1320), ("pause", "Prepare for standing", 30),
                              ("pause", "Standing series 1: load", 5)], "6.17.3 d, e, g")
        kp = f["phases"][4]
        self.assertEqual((kp["positions"], kp["sight_after"], [w["at"] for w in kp["warnings"]], kp.get("early")),
                         ([{"position": "kneeling", "shots": 10}, {"position": "prone", "shots": 10}], "standing", [300, 30], None),
                         "22 minutes run out: standing sighters in the time left (e), 5 minutes and 30 s announced (f)")
        rec = [p for p in f["phases"] if p["kind"] == "record"]
        self.assertEqual([(p["shots"], p["seconds"], p.get("out")) for p in rec[1:]],
                         [(5, 250, None), (5, 250, [8, 7])] + [(1, 50, pl) for pl in (6, 5, 4, 3, 2)], "6.17.3 g, h")
        self.assertTrue(rec[2]["countback"] and sum(p["shots"] for p in rec) == 35)
        self.assertEqual((f["book"], f["scoring"], f["final"], f["face"]), ("ISSF", "decimal", "issf3p", "issf-r50"))


class ThreePositionFinal(Drive, MatchCase):
    def setUp(self):
        super().setUp()
        self.devs = [f"lane-{i}" for i in range(1, 9)] + [f"fin-{i}" for i in range(1, 9)]
        for d in self.devs[2:]:
            self.core.handle_heartbeat({"device_id": d, "boot_count": 1, "uptime_ms": 0})
        self.cc = self.core.competitions
        fid = self.m.save_format({"name": "3x1", "book": "ISSF", "face": "issf-r50", "scoring": "integer", "phases": THREE_P}, "md")["id"]
        self.cid = self.cc.create("League", fid, "md")
        ids = [self.cc.save_competitor(self.cid, {"family": "F%d" % i}) for i in range(8)]
        r = self.cc.add_relay(self.cid, [{"device_id": f"lane-{i + 1}", "competitor_id": p} for i, p in enumerate(ids)], "md")
        self.m.start(r, "md")
        for k in range(3):
            self.at(10 + 100 * k)
            for i in range(8):
                self.shoot(f"lane-{i + 1}", "%.2f" % (0.3 * i))
        self.at(400)

    def test_offered_after_a_three_position_event_only(self):
        kinds = [f["kind"] for f in self.cc.detail(self.cid)["finals_allowed"]]
        self.assertEqual(kinds, ["issf3p"], "the 10 m final follows a standing event")
        with self.assertRaisesRegex(MatchError, "standing event"):
            self.cc.create_final(self.cid, "issf", ["fin-1"], "md")

    def drive3p(self, mid, x_kp, x_st):
        """Run the final like the finalists would: 10 kneeling, Back to sighters, prone sighter, Start record, 10 prone,
        Back to sighters, standing sighter; then the standing series and single shots."""
        for _ in range(400):
            d = self.m.detail(mid)
            if d["state"] == "done":
                return d
            if d["state"] == "holding":
                self.m.command(mid, "go", "ro"); continue
            ph = d["format"]["phases"][d["clock"]["phase"]]
            if ph["kind"] == "record" and ph.get("positions"):
                def step():                                                   # a second between actions, as on a range
                    self.clock.t += 1000
                for l in d["lanes"]:
                    dev = l["target"]
                    for _ in range(10):
                        step(); self.shoot(dev, x_kp(dev))
                    step(); self.m.command(mid, "back_to_sighters", "ro", device_id=dev)
                    step(); self.shoot(dev, "5.00")                           # a prone sighter
                    step(); self.m.command(mid, "start_record", "ro", device_id=dev)
                    for _ in range(10):
                        step(); self.shoot(dev, x_kp(dev))
                    step(); self.m.command(mid, "back_to_sighters", "ro", device_id=dev)   # sighting for standing (e)
                    step(); self.shoot(dev, "5.00")
                lanes = {l["target"]: l for l in self.m.detail(mid)["lanes"]}
                self.assertEqual({(l["between"], l["position"], l["can_record"]) for l in lanes.values()}, {(True, "standing", False)})
                with self.assertRaisesRegex(MatchError, "no more MATCH shots"):
                    self.m.command(mid, "start_record", "ro", device_id=d["lanes"][0]["target"])
                self.assertEqual(self.m.detail(mid)["flags"], [], "sighters between positions and for standing are allowed")
            elif ph["kind"] == "record":
                for l in d["lanes"]:
                    if not self.is_out(mid, l["lane"]):
                        for _ in range(ph["shots"]):
                            self.shoot(l["target"], x_st(l["target"], ph["label"]))
                self.m.tick()
                if self.m.detail(mid)["clock"]["phase"] != d["clock"]["phase"] or self.m.detail(mid)["state"] == "done":
                    continue
            self.clock.t += max(1, self.m.detail(mid)["clock"]["remaining_ms"]); self.m.tick()
        self.fail("the final did not finish")

    def test_two_tied_after_the_second_series_are_ordered_by_countback(self):
        mid = self.cc.create_final(self.cid, "issf3p", self.devs[8:], "md")
        self.assertEqual(self.m.detail(mid)["face"]["id"], "issf-r50", "shot on the qualification's face")
        self.m.start(mid, "md")
        # fin-1 and fin-2: the same total after 30 shots (10.8 x5 + 10.7 x5 standing), fin-2 better in the second series
        st = {"fin-1": {"Standing series 1": "1.00", "Standing series 2": "2.00"}, "fin-2": {"Standing series 1": "2.00", "Standing series 2": "1.00"}}
        single = {"fin-%d" % i: "%.2f" % (8 - i) for i in range(3, 9)}      # single shots: fin-8 10.9, fin-7 10.8 ... fin-3 10.3
        x_st = lambda dev, label: st.get(dev, {}).get(label) or (single.get(dev, "0.00") if label.startswith("Shot") else "0.00")
        self.drive3p(mid, lambda dev: "0.00", x_st)
        places = self.places(mid)
        self.assertEqual((places["fin-1"], places["fin-2"]), (8, 7), "both out after shot 30, by countback: the second standing series (6.17.3 i)")
        log = [r[0] for r in self.core.db.conn().execute("SELECT data FROM match_log WHERE match_id=? AND kind='place' ORDER BY id", (mid,))]
        self.assertIn("6.17.3 i", log[0])
        self.assertFalse(any(p["label"].startswith("Shoot-off") for p in self.m.detail(mid)["format"]["phases"]), "no shoot-off for them")
        self.assertEqual(places, {"fin-1": 8, "fin-2": 7, "fin-3": 6, "fin-4": 5, "fin-5": 4, "fin-6": 3, "fin-7": 2, "fin-8": 1})

    def test_countback_order(self):
        cb = self.m._countback_3p
        a = [100] * 20 + [100] * 5 + [99] * 5
        b = [100] * 20 + [99] * 5 + [100] * 5
        self.assertGreater(cb(b), cb(a), "the second standing series first")
        c = [100] * 19 + [99] + [100] * 10
        d = [99] + [100] * 29
        self.assertGreater(cb(d), cb(c), "then the last prone shot, and on backwards")


class AirPistolFinal(Drive, MatchCase):
    def test_on_the_air_pistol_face(self):
        for d in ("lane-3", "lane-4"):
            self.core.handle_heartbeat({"device_id": d, "boot_count": 1, "uptime_ms": 0})
        cc = self.core.competitions
        fid = self.m.save_format({"name": "AP", "book": "ISSF", "face": "issf-ap10", "scoring": "integer",
                                  "phases": [{"kind": "record", "label": "R", "seconds": 100, "shots": 1, "position": "standing"}]}, "md")["id"]
        cid = cc.create("Pistol", fid, "md")
        ids = [cc.save_competitor(cid, {"family": f}) for f in ("Ames", "Bell")]
        r = cc.add_relay(cid, [{"device_id": f"lane-{i + 1}", "competitor_id": p} for i, p in enumerate(ids)], "md")
        self.m.start(r, "md")
        self.at(10)
        self.shoot("lane-1", "1.00"); self.shoot("lane-2", "2.00")
        self.at(200)
        self.assertEqual([f["kind"] for f in cc.detail(cid)["finals_allowed"]], ["issf"])
        mid = cc.create_final(cid, "issf", ["lane-3", "lane-4"], "md")
        d = self.m.detail(mid)
        self.assertEqual((d["face"]["id"], d["name"].endswith("Air Pistol Final") or "Air Pistol" in d["format"]["name"]), ("issf-ap10", True))
