"""The other finals, each by its own book: ISSF 10 m Air Rifle (6.17.2), CMP 3PAR Standing Start-from-Zero (10.5) and
Three-Position Start-from-Zero (10.3), NRA Precision Air Rifle (7.1.2)."""
from fetserver import matchformats as mf
from fetserver.matches import MatchError
from tests.test_finals import AirGunFinal, FinalCase
from tests.test_matches import MatchCase

THREE_P = [{"kind": "record", "label": k.title(), "seconds": 100, "shots": 1, "position": k} for k in ("kneeling", "prone", "standing")]


class Drive:
    drive, is_out, places = AirGunFinal.drive, AirGunFinal.is_out, AirGunFinal.places

    def phase_lengths(self, mid, label):
        """Seconds the phase with this label ran (phase_start .. phase_end), from the log."""
        rows = self.core.db.conn().execute("SELECT kind, phase, at_ms FROM match_log WHERE match_id=? AND kind IN ('phase_start','go','phase_end') ORDER BY id", (mid,)).fetchall()
        fmt = self.m.detail(mid)["format"]
        i = next(n for n, p in enumerate(fmt["phases"]) if p["label"] == label)
        start = max(r["at_ms"] for r in rows if r["phase"] == i and r["kind"] in ("phase_start", "go"))
        end = next(r["at_ms"] for r in rows if r["phase"] == i and r["kind"] == "phase_end")
        return (end - start) / 1000


class IssfFinal(Drive, FinalCase):
    BOOK = "ISSF"

    def test_course_of_fire(self):
        f = mf.issf_final()
        rec = [p for p in f["phases"] if p["kind"] == "record"]
        self.assertEqual([(p["shots"], p["seconds"]) for p in rec], [(5, 250)] * 2 + [(1, 50)] * 14, "6.17.2 a")
        self.assertTrue(all(p.get("early") and p.get("nohold") for p in rec), "STOP at the time or when all have fired (e, f)")
        self.assertEqual([p.get("out") for p in rec if p.get("out")], [8, 7, 6, 5, 4, 3, 2], "eliminations from shot 12 (g)")
        self.assertEqual([(p["kind"], p["seconds"]) for p in f["phases"][:3]], [("setup", 30), ("sight", 300), ("pause", 60)], "d")
        self.assertEqual((f["book"], f["scoring"], f["final"]), ("ISSF", "decimal", "issf"))
        self.assertEqual([p["label"] for p in mf.issf_final(spoken=True)["phases"]].count("Scores and commentary"), 15)

    def test_draw_eliminations_and_early_stop(self):
        mid = self.cc.create_final(self.cid, "issf", ["lane-5", "lane-4", "lane-3", "lane-2"], "md")
        self.assertEqual(sorted(l["competitor"] for l in self.m.detail(mid)["lanes"]), ["AMES", "BELL", "COLE", "DUNN"])
        self.cc.redraw_final(mid, "md")                                   # placed by draw (6.17.1.2)
        devs = [l["target"] for l in self.m.detail(mid)["lanes"]]
        value = dict(zip(devs, ("0.10", "0.60", "1.20", "2.00")))
        self.m.start(mid, "md")
        self.drive(mid, lambda dev, n, ph: value[dev])
        self.assertEqual(self.places(mid), dict(zip(devs, (1, 2, 3, 4))))
        self.assertEqual(self.phase_lengths(mid, "Series 1"), 0.001, "everyone fired at once: STOP right away (6.17.2 e)")
        f = next(x for x in self.cc.results(self.cid)["finals"] if x["match_id"] == mid)
        self.assertEqual((f["elim"], f["draw"], f["title"], f["so_rule"]), (True, True, "ISSF 10m Air Rifle Final (ISSF 6.17.2)", "ISSF 6.17.2 h"))
        self.assertEqual(f["rows"][0]["total"], f["rows"][0]["final"], "starts from zero (6.17.1.5)")
        with self.assertRaises(MatchError):
            self.m.shoot_off(mid, [1, 2], "md")                           # ties are shot off at once, by the final itself

    def test_a_tie_for_elimination_is_shot_off_by_the_issf_rule(self):
        mid = self.cc.create_final(self.cid, "issf", ["lane-5", "lane-4", "lane-3"], "md")
        devs = [l["target"] for l in self.m.detail(mid)["lanes"]]
        self.m.start(mid, "md")
        d = self.drive(mid, lambda dev, n, ph: "0.00" if dev == devs[0] else "1.00", until=lambda d: d["clock"]["label"].startswith("Shoot-off 1"))
        so = [p for p in d["format"]["phases"] if p["label"].startswith("Shoot-off 1")]
        self.assertEqual([(p["kind"], p["seconds"], p["rule"]) for p in so], [("pause", 5, "6.17.2 h"), ("record", 50, "6.17.2 h")])


class StandingSfz(Drive, FinalCase):
    BOOK = "CMP-3PAR"

    def test_course_of_fire_and_script(self):
        f = mf.cmp_sfz_standing_final()
        self.assertEqual([(p["kind"], p["seconds"]) for p in f["phases"][:3]], [("setup", 120), ("sight", 300), ("pause", 30)],
                         "the script: TAKE YOUR POSITIONS 2 min, 5 min preparation and sighting; 10.2.9 b")
        rec = [p for p in f["phases"] if p["kind"] == "record"]
        self.assertEqual([(p["shots"], p["seconds"], p.get("out")) for p in rec],
                         [(5, 250, None)] * 2 + [(1, 50, mf.ELIM_24.get(n)) for n in range(11, 25)], "10.5.2, 10.5.4 f")
        self.assertTrue(all(p.get("early") for p in rec), "STOP after 250/50 s or when all have fired (10.5.3 c, 10.5.4 d)")
        self.assertEqual(f["book"], "CMP-3PAR")

    def test_places_by_the_final_alone(self):
        mid = self.cc.create_final(self.cid, "sfz_standing", ["lane-5", "lane-4", "lane-3"], "md", n=3)
        devs = [l["target"] for l in self.m.detail(mid)["lanes"]]
        self.m.start(mid, "md")
        self.drive(mid, lambda dev, n, ph: {devs[0]: "2.00", devs[1]: "0.10", devs[2]: "1.00"}[dev])
        self.assertEqual(self.places(mid), {devs[1]: 1, devs[2]: 2, devs[0]: 3})


class ThreePositionSfz(Drive, MatchCase):
    """3PAR 10.3: 8 finalists; after the second standing series 8th AND 7th are eliminated; shots 31-35."""
    def setUp(self):
        super().setUp()
        self.devs = [f"lane-{i}" for i in range(1, 9)] + [f"fin-{i}" for i in range(1, 9)]
        for d in self.devs[2:]:
            self.core.handle_heartbeat({"device_id": d, "boot_count": 1, "uptime_ms": 0})
        self.cc = self.core.competitions
        fid = self.m.save_format({"name": "3x1", "book": "CMP-3PAR", "phases": THREE_P}, "md")["id"]
        self.cid = self.cc.create("League", fid, "md")
        ids = [self.cc.save_competitor(self.cid, {"family": "F%d" % i}) for i in range(8)]
        r = self.cc.add_relay(self.cid, [{"device_id": f"lane-{i + 1}", "competitor_id": p} for i, p in enumerate(ids)], "md")
        self.m.start(r, "md")
        for k in range(3):
            self.at(10 + 100 * k)
            for i in range(8):
                self.shoot(f"lane-{i + 1}", "%.2f" % (0.3 * i))
        self.at(400)

    def test_offered_only_after_a_three_position_event(self):
        kinds = [f["kind"] for f in self.cc.detail(self.cid)["finals_allowed"]]
        self.assertEqual(kinds, ["standard", "sfz_3p"], "no standing Start-from-Zero final after a 3-position event (10.5)")
        with self.assertRaises(MatchError) as ctx:
            self.cc.create_final(self.cid, "sfz_standing", ["fin-1"], "md")
        self.assertIn("follows a standing event", str(ctx.exception))

    def test_course_of_fire(self):
        f = mf.cmp_sfz_3p_final()
        rec = [p for p in f["phases"] if p["kind"] == "record"]
        self.assertEqual([(p["position"], p["shots"], p["seconds"], p.get("out")) for p in rec],
                         [("kneeling", 10, 400, None), ("prone", 10, 300, None), ("standing", 5, 250, None), ("standing", 5, 250, [8, 7])]
                         + [("standing", 1, 50, pl) for pl in (6, 5, 4, 3, 2)], "10.3.1, 10.3.3 f")
        self.assertEqual(sum(p["shots"] for p in rec), 35)
        sights = [(p["position"], p["seconds"]) for p in f["phases"] if p["kind"] == "sight"]
        self.assertEqual(sights, [("kneeling", 300), ("prone", 300), ("standing", 420)], "5 min prone changeover as the script says")

    def run_final(self, x_of, until=None):
        mid = self.cc.create_final(self.cid, "sfz_3p", self.devs[8:], "md")
        self.lane_dev = {l["lane"]: l["target"] for l in self.m.detail(mid)["lanes"]}
        self.m.start(mid, "md")
        return mid, self.drive(mid, x_of, until=until)

    def test_eighth_and_seventh_out_together(self):
        mid, d = self.run_final(lambda dev, n, ph: "%.2f" % (0.5 * int(dev[-1])), until=lambda d: d["clock"]["label"] == "Shot 31: load")
        self.assertEqual({k: v for k, v in self.places(mid).items() if v}, {"fin-8": 8, "fin-7": 7}, "both after shot 30 (10.3.1)")
        self.drive(mid, lambda dev, n, ph: "%.2f" % (0.5 * int(dev[-1])))
        self.assertEqual(self.places(mid), {f"fin-{i}": i for i in range(1, 9)})

    def test_ties_at_the_double_elimination(self):
        # fin-7 and fin-8 equal (both out: who is 8th is shot off), fin-5 and fin-6 equal too but safe
        val = lambda dev: {"fin-8": "2.00", "fin-7": "2.00", "fin-6": "1.00", "fin-5": "1.00"}.get(dev, "0.%d0" % int(dev[-1]))
        def x(dev, n, ph):
            if ph.get("lanes"):
                return "0.00" if dev == "fin-7" else "1.50"
            return val(dev)
        mid, d = self.run_final(x, until=lambda d: d["clock"]["label"] == "Shot 31: load")
        self.assertEqual({k: v for k, v in self.places(mid).items() if v}, {"fin-8": 8, "fin-7": 7}, "the shoot-off decided 8th, then 7th")
        so = [p for p in d["format"]["phases"] if p.get("lanes") and p["kind"] == "record"]
        self.assertEqual((len(so), so[0]["rule"], sorted(self.lane_dev[n] for n in so[0]["lanes"])), (1, "10.2.9 n", ["fin-7", "fin-8"]))

    def test_tie_across_the_seventh_place(self):
        # fin-8 clearly last; fin-7 and fin-6 equal for 7th: shoot-off between them only
        val = lambda dev: {"fin-8": "3.00", "fin-7": "2.00", "fin-6": "2.00"}.get(dev, "0.%d0" % int(dev[-1]))
        def x(dev, n, ph):
            if ph.get("lanes"):
                return "0.00" if dev == "fin-6" else "1.50"
            return val(dev)
        mid, d = self.run_final(x, until=lambda d: d["clock"]["label"] == "Shot 31: load")
        self.assertEqual({k: v for k, v in self.places(mid).items() if v}, {"fin-8": 8, "fin-7": 7})
        so = [p for p in d["format"]["phases"] if p.get("lanes") and p["kind"] == "record"]
        self.assertEqual(sorted(self.lane_dev[n] for n in so[0]["lanes"]), ["fin-6", "fin-7"])


class StandingEvent(MatchCase):
    def test_standing_sfz_offered_after_a_standing_event(self):
        fid = self.m.save_format({"name": "S", "book": "CMP-3PAR", "phases": [{"kind": "record", "label": "R", "seconds": 100, "shots": 1, "position": "standing"}]}, "md")["id"]
        cid = self.core.competitions.create("S", fid, "md")
        self.assertEqual([f["kind"] for f in self.core.competitions.detail(cid)["finals_allowed"]], ["standard", "sfz_standing"])


class NraFinal(Drive, FinalCase):
    BOOK = "NRA"

    def test_course_of_fire(self):
        f = mf.nra_final()
        self.assertEqual([(p["kind"], p["seconds"]) for p in f["phases"][:3]], [("setup", 180), ("sight", 420), ("pause", 30)], "7.1.2 d-g")
        shot = f["phases"][3:6]
        self.assertEqual([(p["kind"], p["seconds"], p["start"]) for p in shot],
                         [("pause", 5, "For the first competition shot. Load."), ("pause", 4, "Attention. Three, two, one."), ("record", 75, "Start.")], "7.1.2 m")
        self.assertTrue(shot[1].get("nohold") and shot[2].get("early"), "STOP after the last shooter has fired (m)")
        self.assertEqual(sum(p["shots"] for p in f["phases"] if p["kind"] == "record"), 10)

    def test_added_to_qualification_positions_by_rank_and_any_tie_shot_off(self):
        mid = self.cc.create_final(self.cid, "nra", ["lane-5", "lane-4", "lane-3", "lane-2"], "md")
        self.assertEqual([l["competitor"] for l in self.m.detail(mid)["lanes"]], ["AMES", "BELL", "COLE", "DUNN"], "by rank (7.1.2 k)")
        with self.assertRaises(MatchError):
            self.cc.redraw_final(mid, "md")
        self.m.start(mid, "md")
        # finals equal for everyone: Ames = Bell tie for 1st; Cole and Dunn differ by qualification
        self.drive(mid, lambda dev, n, ph: "1.00")
        f = next(x for x in self.cc.results(self.cid)["finals"] if x["match_id"] == mid)
        self.assertFalse(f["elim"])
        rows = {r["device_id"]: r for r in f["rows"]}
        self.assertEqual(rows["lane-5"]["total"], round(rows["lane-5"]["qual"] + rows["lane-5"]["final"], 1), "added (7.1.2 o)")
        self.assertEqual(f["shoot_off_needed"], [[1, 2]])
        self.m.shoot_off(mid, [1, 2], "md")
        so = [p for p in self.m.detail(mid)["format"]["phases"] if p.get("lanes")]
        self.assertEqual([(p["kind"], p["seconds"]) for p in so], [("pause", 5), ("pause", 4), ("record", 75)], "the same procedure (q)")

    def test_ties_below_the_medals_are_shot_off_too(self):
        rows = [{"total": t, "shoot_off": [], "dq": None, "final": 0, "summary": None, "lane": n} for n, t in ((1, 300.0), (2, 290.0), (3, 280.0), (4, 280.0))]
        ranked = self.cc._rank_final(rows, places=8)
        self.assertEqual([(r["lane"], r["place"], r["tied"]) for r in ranked], [(1, 1, False), (2, 2, False), (3, 3, True), (4, 3, True)])
        ranked = self.cc._rank_final([dict(r) for r in rows] + [{"total": 270.0, "shoot_off": [], "dq": None, "final": 0, "summary": None, "lane": 5},
                                                              {"total": 270.0, "shoot_off": [], "dq": None, "final": 0, "summary": None, "lane": 6}], places=8)
        self.assertEqual(self.cc._ties_for_medals(ranked, 8), [[3, 4], [5, 6]], "NRA: every tie (7.1.2 q)")
        self.assertEqual(self.cc._ties_for_medals(ranked, 3), [[3, 4]], "3PAR: only for the medals")


class NraTies(Drive, FinalCase):
    BOOK = "NRA"

    def test_a_tie_for_fourth_is_shot_off(self):
        eve = self.cc.save_competitor(self.cid, {"family": "Eve"})
        r2 = self.cc.add_relay(self.cid, [{"device_id": "lane-5", "competitor_id": eve}], "md")
        self.m.start(r2, "md")
        self.at(210); self.shoot("lane-5", "2.00"); self.shoot("lane-5", "2.00")        # 0.8 above Dunn
        self.at(400)
        mid = self.cc.create_final(self.cid, "nra", ["lane-1", "lane-2", "lane-3", "lane-4", "lane-5"], "md")
        dev = {l["competitor"]: l["target"] for l in self.m.detail(mid)["lanes"]}
        x = {dev["AMES"]: "0.00", dev["BELL"]: "0.50", dev["COLE"]: "0.00", dev["DUNN"]: "0.00"}
        eve = {1: "2.00", 2: "0.50"}                     # Eve's final 0.8 below Dunn's: equal totals, different finals
        self.m.start(mid, "md")
        self.drive(mid, lambda d, n, ph: eve.get(n, "0.00") if d == dev["EVE"] else x[d])
        f = next(y for y in self.cc.results(self.cid)["finals"] if y["match_id"] == mid)
        self.assertEqual([(r["name"], r["place"], r["tied"]) for r in f["rows"]][:3], [("AMES", 1, False), ("COLE", 2, False), ("BELL", 3, False)])
        self.assertEqual(sorted((r["name"], r["place"], r["tied"]) for r in f["rows"][3:]), [("DUNN", 4, True), ("EVE", 4, True)],
                         "not split by the final score (that is 3PAR 8.6.2 for places below the medals): shot off")
        lanes = {l["competitor"]: l["lane"] for l in self.m.detail(mid)["lanes"]}
        self.assertEqual(f["shoot_off_needed"], [sorted([lanes["DUNN"], lanes["EVE"]])], "NRA 7.1.2 q: every tie, not only the medals")


class WholeRingQualification(Drive, MatchCase):
    def test_the_final_is_scored_in_tenths(self):
        self.cc = self.core.competitions
        fid = self.m.save_format({"name": "Q", "book": "NRA", "scoring": "integer", "phases": [{"kind": "record", "label": "R", "seconds": 100, "shots": 1}]}, "md")["id"]
        cid = self.cc.create("League", fid, "md")
        p = self.cc.save_competitor(cid, {"family": "Ames"})
        r = self.cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": p}], "md")
        self.m.start(r, "md")
        self.at(10); self.shoot("lane-1", "0.00")
        self.at(200)
        mid = self.cc.create_final(cid, "nra", ["lane-2"], "md")
        self.m.start(mid, "md")
        self.drive(mid, lambda d, n, ph: "0.00")
        row = next(y for y in self.cc.results(cid)["finals"] if y["match_id"] == mid)["rows"][0]
        self.assertEqual((row["qual"], row["final"], row["total"]), (10, 108.0, 118.0), "whole-ring qualification + a final in tenths (7.1.2 o)")


class FinalGuidance(MatchCase):
    def test_firing_out_of_turn(self):
        from fetserver.rules import guidance
        self.assertIn("no penalty (ISSF 6.17.1.14 m)", guidance("not_in_shoot_off", "record", "scored", "ISSF")["text"])
        self.assertIn("no penalty (ISSF 6.17.1.14 m)", guidance("eliminated", "record", "scored", "ISSF")["text"])
        self.assertIn("(ISSF 6.17.2 h)", guidance("not_in_shoot_off", "record", "scored", "ISSF")["text"])
        for book in ("CMP-3PAR", "CMP-AG", "NRA", "NONE"):
            self.assertNotIn("penalty", guidance("eliminated", "record", "scored", book)["text"], book)


class FinalOnTheBoard(Drive, FinalCase):
    BOOK = "ISSF"

    def board(self, mid):
        return next(b for b in self.m.board([mid]) if b["id"] == mid)["final"]

    def test_where_the_final_is_and_what_comes_next(self):
        mid = self.cc.create_final(self.cid, "issf", ["lane-5", "lane-4", "lane-3"], "md")
        f = self.board(mid)
        self.assertEqual((f["shots_done"], f["shots_total"], f["next_out"], f["shoot_off"], f["elim"]),
                         (0, 24, {"after": 12, "places": [8]}, None, True), "before the start: the first decision is after shot 12")
        self.assertNotIn("final", next(b for b in self.m.board([self.r1]) if b["id"] == self.r1), "a qualification relay: the usual board")
        devs = [l["target"] for l in self.m.detail(mid)["lanes"]]
        value = dict(zip(devs, ("0.00", "1.00", "2.00")))
        self.m.start(mid, "md")
        f = self.board(self.drive(mid, lambda d, n, ph: value[d], until=lambda d: d["clock"]["label"] == "Shot 15")["id"])
        self.assertEqual((f["shots_done"], f["now"], f["next_out"]), (14, "Shot 15", {"after": 16, "places": [6]}))
        self.assertEqual([r["last"] for r in f["rows"]], [10.8, 10.5, 10.1], "everyone's last shot")
        self.drive(mid, lambda d, n, ph: value[d])
        f = self.board(mid)
        self.assertEqual((f["shots_done"], f["next_out"], f["now"]), (24, None, None))
        self.assertEqual([(r["place"], r["out"]) for r in f["rows"]], [(1, 1), (2, 2), (3, 3)])

    def test_a_shoot_off_on_the_board(self):
        mid = self.cc.create_final(self.cid, "issf", ["lane-5", "lane-4", "lane-3"], "md")
        devs = [l["target"] for l in self.m.detail(mid)["lanes"]]
        self.m.start(mid, "md")
        d = self.drive(mid, lambda dev, n, ph: "0.00" if dev == devs[0] else "1.00", until=lambda d: d["clock"]["label"] == "Shoot-off 1")
        f = self.board(mid)
        lanes = {l["target"]: l["lane"] for l in d["lanes"]}
        self.assertEqual(f["shoot_off"], sorted([lanes[devs[1]], lanes[devs[2]]]))
        self.assertEqual(f["next_out"], {"after": 22, "places": [3]}, "the shoot-off decides the place of shot 22")
