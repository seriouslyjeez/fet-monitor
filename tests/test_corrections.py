"""Corrections by the range officials: reviews and protests, overrides, annulled and added shots, crossfires (ISSF
6.11.6, CMP 7.17.9, NRA 10.10.4), penalties and disqualification. The shots themselves never change."""
from fetserver.matches import MatchError
from tests.test_matches import MatchCase


class CorrCase(MatchCase):
    def setUp(self):
        super().setUp()
        self.cc = self.core.corrections

    def match(self, kinds, lanes=("lane-1", "lane-2")):
        mid = self.m.create("M", self.custom(kinds), [{"device_id": d, "competitor": d.upper()} for d in lanes], "dir")
        self.m.start(mid, "dir")
        return mid

    def row(self, mid, lane):
        return next(r for r in self.m.results(mid)["rows"] if r["lane"] == lane)

    def shots(self, mid, lane, stream="scored"):
        return [s for s in self.cc.lane_shots(mid, lane)["shots"] if s["stream"] == stream]

    def counted(self, mid, lane):
        c = self.core.db.conn()
        m = self.m._row(c, mid)
        ln = next(l for l in self.m._lanes(c, mid) if l["lane"] == lane)
        return self.m.counted(c, m, ln, {f["shot_id"]: f for f in self.m.detail(mid)["flags"]})


class ShotCorrections(CorrCase):
    def test_override_keeps_the_original_and_can_be_undone(self):
        mid = self.match([("record", 600, 3)])
        self.at(10); self.shoot("lane-1", "1.00"); self.shoot("lane-1", "1.00")
        first = self.shots(mid, 1)[0]
        before = self.row(mid, 1)["total"]
        cid = self.cc.add(mid, "override", 1, "md", True, first["id"], {"value": 9.5, "note": "plug gauge"})
        x = self.counted(mid, 1)[0]
        self.assertEqual((x["tenths"], x["ring"], x["orig"]["tenths"], x["override"]), (95, 9, round(first["decimal"] * 10), "plug gauge"))
        self.assertAlmostEqual(self.row(mid, 1)["total"], before - first["decimal"] + 9.5)
        self.cc.void(mid, cid, "md")
        self.assertAlmostEqual(self.row(mid, 1)["total"], before, msg="void: back to the fired value")
        for bad in ({"value": 11}, {"value": -1}, {"value": "9"}, {"value": 9.5}):    # the last: no reason
            with self.assertRaises(MatchError, msg=bad):
                self.cc.add(mid, "override", 1, "md", True, first["id"], bad)

    def test_annul_and_added_shots_take_their_place(self):
        mid = self.match([("record", 600, 3)])
        self.at(10); self.shoot("lane-1"); self.at(20); self.shoot("lane-1"); self.at(30); self.shoot("lane-1")
        s1, s2, s3 = self.shots(mid, 1)
        self.cc.add(mid, "annul", 1, "md", True, s2["id"], {"note": "disclaimed, RO confirmed", "rule": "ISSF 6.11.6.7"})
        self.cc.add(mid, "added", 1, "md", True, None, {"value": 10.4, "after": 1, "note": "failed to register (6.10.9.3)"})
        c = self.counted(mid, 1)
        self.assertEqual([(x["n"], x["tenths"], bool(x.get("added"))) for x in c], [(1, round(s1["decimal"] * 10), False), (None, 104, True), (3, round(s3["decimal"] * 10), False)])
        self.assertEqual(self.row(mid, 1)["removed"][0]["why"], "disclaimed, RO confirmed")
        self.assertEqual(self.flags(mid), [], "annulled + added: still three shots")
        self.cc.add(mid, "added", 1, "md", True, None, {"miss": True, "after": 1, "note": "x"})
        self.assertEqual(self.flags(mid), [("lane-1", 3, "scored", "more than 3 record shots in record 0")], "an added shot counts toward the limit")
        with self.assertRaises(MatchError):
            self.cc.add(mid, "override", 1, "md", True, s2["id"], {"value": 9, "note": "x"})          # annulled already
        with self.assertRaises(MatchError):
            self.cc.add(mid, "added", 1, "md", True, None, {"value": 9, "after": 9, "note": "x"})    # no shot 9

    def test_crossfire_record_shot(self):
        mid = self.match([("record", 600, 2)])
        self.at(10); self.shoot("lane-2", "0.50")              # lane-1's shot, landed on lane-2
        self.at(20); self.shoot("lane-2", "1.00"); self.shoot("lane-2", "1.20")
        self.at(30); self.shoot("lane-1"); self.shoot("lane-1")
        hit = self.shots(mid, 2)[0]
        self.cc.add(mid, "crossfire", 2, "md", True, hit["id"], {"from_lane": 1})
        self.assertEqual([x["n"] for x in self.counted(mid, 2)], [2, 3], "annulled on the firing point that received it")
        c1 = self.counted(mid, 1)
        self.assertEqual([(x["miss"], x.get("crossfire_to")) for x in c1[:1]], [(True, 2)], "a miss for the firer, at that moment")
        self.assertEqual([f[1] for f in self.flags(mid) if f[0] == "lane-1"], [2], "so the firer's last shot is one too many")
        self.assertEqual(self.flags(mid)[-1][3], "more than 2 record shots in record 0")
        with self.assertRaises(MatchError):
            self.cc.add(mid, "crossfire", 2, "md", True, hit["id"], {"from_lane": 1})      # already annulled
        for bad in (2, 9, "1"):
            with self.assertRaises(MatchError, msg=bad):
                self.cc.add(mid, "crossfire", 2, "md", True, self.shots(mid, 2)[1]["id"], {"from_lane": bad})

    def test_crossfire_source_unknown(self):
        mid = self.match([("record", 600, 2)])
        self.at(10); self.shoot("lane-2"); self.shoot("lane-2"); self.shoot("lane-2")
        self.cc.add(mid, "crossfire", 2, "md", True, self.shots(mid, 2)[0]["id"], {"from_lane": None})
        self.assertEqual((len(self.counted(mid, 2)), self.counted(mid, 1)), (2, []), "annulled; nobody gets a miss")
        self.assertEqual(self.row(mid, 2)["removed"][0]["why"], "crossfire (source unknown)")

    def test_sighter_crossfired_onto_a_record_target(self):
        mid = self.match([("stage", 600, 3)])                   # NRA-style stage: each shooter starts record himself
        self.m.command(mid, "start_record", "ro", device_id="lane-2")
        self.at(10); self.shoot("lane-2", "0.30")               # lane-1 still sighting: its sighter hits lane-2's record target
        self.at(15); self.m.command(mid, "start_record", "ro", device_id="lane-1")
        self.at(20); self.shoot("lane-1", "0.00")
        fired = round(self.shots(mid, 1)[0]["decimal"] * 10)
        self.cc.add(mid, "crossfire", 2, "md", True, self.shots(mid, 2)[0]["id"], {"from_lane": 1})
        self.assertEqual(self.counted(mid, 2), [])
        x = self.counted(mid, 1)
        self.assertEqual((len(x), x[0]["tenths"], x[0]["penalties"]), (1, fired - 20, ["ISSF 6.11.6.3"]), "-2 from the firer's first series, no miss")


    def test_sighter_onto_a_sighting_target_costs_nothing(self):
        mid = self.match([("sight", 600, 0), ("record", 600, 2)])
        self.at(10); self.shoot("lane-2")                       # both sighting (ISSF 6.11.6.2)
        self.at(700); self.shoot("lane-1")
        before = self.counted(mid, 1)
        self.cc.add(mid, "crossfire", 2, "md", True, self.shots(mid, 2, "sighter")[0]["id"], {"from_lane": 1})
        self.assertEqual(self.counted(mid, 1), before, "no miss, no penalty for the firer")


class ReviewsPenaltiesDq(CorrCase):
    def test_officer_marks_director_settles(self):
        mid = self.match([("record", 600, 3)])
        self.at(10); self.shoot("lane-1", "0.00")
        sid = self.shots(mid, 1)[0]["id"]
        pid = self.cc.add(mid, "protest", 1, "ro", False, sid, {"note": "shooter says 10.9 shown as 9.x"})
        self.assertEqual(self.m.detail(mid)["open_corrections"], 1)
        for kind, data in (("override", {"value": 9, "note": "x"}), ("penalty", {"points": 2, "rule": "x"}), ("dq", {"rule": "x", "official": "y"})):
            with self.assertRaises(MatchError, msg=kind):
                self.cc.add(mid, kind, 1, "ro", False, sid if kind == "override" else None, data)
        with self.assertRaises(MatchError):
            self.cc.settle(mid, pid, "maybe", "md")
        fired = round(self.shots(mid, 1)[0]["decimal"] * 10)
        self.cc.settle(mid, pid, "not_upheld", "md", penalty=2)
        x = self.counted(mid, 1)[0]
        self.assertEqual((x["tenths"], x["penalties"]), (fired - 20, ["ISSF 6.16.5.2 c"]), "2 points off the protested shot")
        self.assertEqual(self.m.detail(mid)["open_corrections"], 0)
        with self.assertRaises(MatchError):
            self.cc.settle(mid, pid, "upheld", "md")            # settled already
        rid = self.cc.add(mid, "review", 1, "ro", False, sid)
        with self.assertRaises(MatchError):
            self.cc.settle(mid, rid, "upheld", "md", penalty=2)  # a penalty only for a protest not upheld

    def test_penalty_on_the_first_series_and_rule_required(self):
        mid = self.match([("record", 600, 12)])
        self.at(10)
        for x in ("2.00", "0.00", "1.00"):
            self.shoot("lane-1", x)
        with self.assertRaises(MatchError):
            self.cc.add(mid, "penalty", 1, "md", True, None, {"points": 2})
        self.cc.add(mid, "penalty", 1, "md", True, None, {"points": 2, "rule": "CMP 7.22.1", "note": "position"})
        fired = [round(x["decimal"] * 10) for x in self.shots(mid, 1)]
        c = self.counted(mid, 1)
        self.assertEqual([x["tenths"] for x in c], [fired[0], fired[1] - 20, fired[2]], "off the best shot of the first series")
        self.assertEqual(self.row(mid, 1)["penalties"], [{"points": 2, "rule": "CMP 7.22.1", "note": "position",
                                                           "text": "Open rule violation: a warning first, then 2 points off, then disqualification."}])

    def test_disqualified(self):
        mid = self.match([("record", 600, 3)])
        self.at(10); self.shoot("lane-1", "0.00"); self.shoot("lane-2", "3.00")
        for bad in ({"rule": "CMP 7.22.2"}, {"official": "J. Doe"}):
            with self.assertRaises(MatchError):
                self.cc.add(mid, "dq", 1, "md", True, None, bad)
        cid = self.cc.add(mid, "dq", 1, "md", True, None, {"rule": "CMP 7.22.2", "official": "J. Doe", "note": "illegal sling"})
        rows = self.m.results(mid)["rows"]
        self.assertEqual([(r["lane"], r["place"], r["total"], bool(r["dq"])) for r in rows], [(2, 1, rows[0]["total"], False), (1, None, 0.0, True)])
        self.assertEqual(rows[1]["dq"]["official"], "J. Doe")
        with self.assertRaises(MatchError):
            self.cc.add(mid, "dq", 1, "md", True, None, {"rule": "x", "official": "y"})
        self.cc.void(mid, cid, "md")
        self.assertEqual(self.m.results(mid)["rows"][0]["lane"], 1, "voided: back in first place")

    def test_not_before_the_start_and_bad_input(self):
        mid = self.m.create("M", self.custom([("record", 60, 1)]), [{"device_id": "lane-1"}], "dir")
        with self.assertRaises(MatchError):
            self.cc.add(mid, "dq", 1, "md", True, None, {"rule": "x", "official": "y"})
        self.m.start(mid, "dir")
        for kind, lane, shot in (("nope", 1, None), ("review", 9, None), ("review", 1, None), ("review", 1, 999)):
            with self.assertRaises(MatchError, msg=kind):
                self.cc.add(mid, kind, lane, "md", True, shot, {})


class RuleText(CorrCase):
    def test_references_become_text(self):
        from fetserver.rules import explain
        for ref, book, want in (("ISSF 6.11.6.3", "CMP", "2 points off the firer's first series"), ("6.11.6.3", "ISSF", "first series"),
                                ("issf 6.12.6.1a", "CMP", "Open rule violation"), ("ISSF 6.12.6.1 (b)", "CMP", "concealed"),
                                ("Rule 7.22.2", "CMP", "two match officials"), ("USAS 6.16.5.2 c", "NRA", "not upheld"),
                                ("NRA 10.10.4 a", "ISSF", "crossfired"), ("6.11.6.3 b", "ISSF", "first series")):
            self.assertIn(want, explain(ref, book)["text"] or "", ref)
        for ref in ("ISSF 99.1", "local rule 4", "", None, "6.12.6.1 z"):
            self.assertIsNone(explain(ref, "ISSF")["text"], ref)


class TeamWithADisqualifiedMember(CorrCase):
    def test_issf_team_not_ranked_cmp_member_counts_zero(self):
        for fid, want in (("issf-ar60", "DSQ: a member was disqualified (ISSF 6.12.6.2 g)"), ("cmp-3x20", None)):
            cc = self.core.competitions
            cid = cc.create("L " + fid, fid, "md", team_size=2)
            a = cc.save_competitor(cid, {"family": "Ames", "team": "Red"})
            b = cc.save_competitor(cid, {"family": "Bell", "team": "Red"})
            rid = cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}, {"device_id": "lane-2", "competitor_id": b}], "md")
            self.m.start(rid, "md")
            self.cc.add(rid, "dq", 2, "md", True, None, {"rule": "x", "official": "y"})
            team = cc.results(cid)["classes"][0]["teams"][0]
            self.assertEqual((team["place"], team.get("reason")), (None, want) if want else (1, None), fid)
            self.m.command(rid, "abort", "md")


class OfficialResults(CorrCase):
    def test_only_when_everything_is_settled_and_then_frozen(self):
        mid = self.match([("setup", 60, 0), ("record", 60, 2)])
        self.at(5); self.shoot("lane-1")                        # flagged: during setup
        self.at(70); self.shoot("lane-1"); self.shoot("lane-1")
        sid = self.shots(mid, 1, "scored")[0]["id"]
        pid = self.cc.add(mid, "protest", 1, "ro", False, sid, {})
        with self.assertRaises(MatchError) as ctx:
            self.m.make_official(mid, "md")
        self.assertIn("the match has not finished", str(ctx.exception))
        self.at(200)
        self.assertEqual(self.m.detail(mid)["open_items"], ["1 flagged shot not decided", "1 review or protest not settled"])
        flag = self.m.detail(mid)["flags"][0]["shot_id"]
        self.m.decide(mid, flag, "ignore", "md")
        self.cc.settle(mid, pid, "withdrawn", "md")
        self.m.make_official(mid, "md")
        d = self.m.detail(mid)
        self.assertEqual((d["official_by"], d["open_items"], self.m.results(mid)["official_ms"] is not None), ("md", [], True))
        for fn in (lambda: self.m.decide(mid, flag, "penalty2_first", "md"), lambda: self.cc.add(mid, "review", 1, "ro", False, sid, {}),
                   lambda: self.cc.void(mid, pid, "md"), lambda: self.m.restart(mid, 0, "md"), lambda: self.m.make_official(mid, "md")):
            with self.assertRaises(MatchError):
                fn()
        self.m.reopen(mid, "md")
        self.cc.add(mid, "review", 1, "ro", False, sid, {})       # changes are possible again
        self.assertIsNone(self.m.detail(mid)["official_ms"])
        with self.assertRaises(MatchError):
            self.m.reopen(mid, "md")

    def test_a_competition_is_official_when_every_relay_is(self):
        cc = self.core.competitions
        fid = self.custom([("record", 60, 1)])
        cid = cc.create("L", fid, "md")
        a, b = cc.save_competitor(cid, {"family": "Ames"}), cc.save_competitor(cid, {"family": "Bell"})
        r1 = cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}], "md")
        self.m.start(r1, "md"); self.at(100); self.m.make_official(r1, "md")
        r2 = cc.add_relay(cid, [{"device_id": "lane-2", "competitor_id": b}], "md")
        self.assertIsNone(cc.results(cid)["official_ms"], "relay 2 not yet")
        self.m.start(r2, "md"); self.at(200); self.m.make_official(r2, "md")
        self.assertIsNotNone(cc.results(cid)["official_ms"])


class DecisionGuidance(CorrCase):
    def test_flags_carry_the_rule_and_a_suggestion(self):
        mid = self.match([("setup", 60, 0), ("record", 60, 1)])
        self.at(5); self.shoot("lane-1")                        # during setup: before START (ISSF 6.11.1.1 h)
        self.at(70); self.shoot("lane-1"); self.shoot("lane-1")  # one too many (ISSF 6.11.5)
        self.at(200); self.shoot("lane-1")                      # after the end (ISSF 6.11.1.3 a)
        g = {f["why"]: f["guide"] for f in self.m.detail(mid)["flags"]}
        self.assertEqual((g["during setup 0 (no firing)"]["rule"], g["during setup 0 (no firing)"]["suggest"]), ("ISSF 6.11.1.1 h", "miss_first"))
        self.assertEqual((g["more than 1 record shots in record 1"]["rule"], g["more than 1 record shots in record 1"]["suggest"]), ("ISSF 6.11.5", "annul"))
        self.assertIn("2-point penalty", g["more than 1 record shots in record 1"]["todo"])
        self.assertEqual((g["after the end"]["rule"], g["after the end"]["suggest"]), ("ISSF 6.11.1.3 a", "ignore"))
        self.assertEqual(g["after the end"]["url"], "https://issf-sports.org/rules")

    def test_each_rulebook(self):
        from fetserver.rules import guidance
        cases = [(("before_cmd", "record", "sighter", "CMP"), ("CMP 7.17.2", "penalty2_first")),
                 (("before_cmd", "sight", "sighter", "CMP"), ("CMP 7.17.1", "miss_first")),
                 (("before_start", None, "sighter", "NRA"), ("NRA 14.5", None)),
                 (("sighter_after_record", "stage", "sighter", "NRA"), ("NRA 9.2 d", "penalty2_first")),
                 (("sighter_in_record", "record", "sighter", "ISSF"), ("ISSF 6.11.1.2 d", None)),
                 (("paused", "record", "scored", "CMP"), ("CMP 7.17.3", "count")),
                 (("after_end", None, "scored", "ISSF"), ("ISSF 6.11.1.3 a", "miss")),
                 (("no_fire", "changeover", "sighter", "CMP"), ("CMP 7.17.2", None)),
                 (("restarted", "record", "scored", "ISSF"), (None, "annul")),
                 (("moving", "record", "sighter", "CMP"), (None, "ignore")),
                 (("before_start", None, "scored", "ISSF"), ("ISSF 6.11.1.1 h", "miss")),
                 (("too_many", "record", "scored", "Custom"), ("ISSF 6.11.5", "annul"))]
        for args, (rule, suggest) in cases:
            g = guidance(*args)
            self.assertEqual((g["rule"], g["suggest"]), (rule, suggest), args)
            self.assertTrue(g["text"])
            if suggest is None and rule:
                self.assertTrue(g["todo"], args)
