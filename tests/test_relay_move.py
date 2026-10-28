"""CMP 3PAR 7.18 (CMP AG 7.12): a shooter whose target fails finishes the match on a later relay: their shots so far stay, they wait until
the stage they left, then have the time they had left plus 2 minutes (7.18.3) and sighters first, and only the record
shots still owed; the results join both parts."""
from fetserver.matches import MatchError
from tests.test_matches import MatchCase, tiny

FORMAT = [("sight", 100, 0), ("record", 300, 4), ("changeover", 60, 0), ("sight", 100, 0), ("record", 300, 4)]


class LaterRelay(MatchCase):
    BOOK = "CMP-3PAR"

    def setUp(self):
        super().setUp()
        for d in ("lane-3", "lane-4"):
            self.core.handle_heartbeat({"device_id": d, "boot_count": 1, "uptime_ms": 0})
        self.cc = self.core.competitions
        fid = self.m.save_format(dict(tiny(FORMAT), name="Two stages", book=self.BOOK), "md")["id"]
        self.cid = self.cc.create("League", fid, "md")
        self.ames = self.cc.save_competitor(self.cid, {"family": "Ames"})
        self.bell = self.cc.save_competitor(self.cid, {"family": "Bell"})
        self.r1 = self.cc.add_relay(self.cid, [{"device_id": "lane-1", "competitor_id": self.ames}, {"device_id": "lane-2", "competitor_id": self.bell}], "md")
        self.m.start(self.r1, "md")

    def entry(self, family):
        return next(e for cl in self.cc.results(self.cid)["classes"] for e in cl["individuals"] if e["family"] == family)

    def lane(self, mid, did):
        return next(l for l in self.m.detail(mid)["lanes"] if l["device_id"] == did)

    def test_rest_of_the_match_on_a_new_relay(self):
        self.at(110); self.shoot("lane-1"); self.at(120); self.shoot("lane-1")
        self.at(200)                                            # lane-1 fails: 200 s of the record stage left
        r2 = self.cc.finish_on_later_relay(self.r1, "lane-1", None, "lane-3", "md")
        d2 = self.m.detail(r2)
        self.assertEqual((d2["relay"], d2["state"], [l["competitor"] for l in d2["lanes"]]), (2, "ready", ["AMES"]), "a new relay")
        self.assertEqual(self.lane(self.r1, "lane-1")["own"], "left")
        self.assertEqual((self.entry("Ames")["shots"], self.cc.detail(self.cid)["competitors"][0]["relay"]), (2, 2), "shots kept; now on relay 2")
        self.at(300); self.shoot("lane-1")                     # the failed target no longer counts for anyone
        self.at(570)                                            # relay 1's second record stage: the failed target stays out of it
        self.assertNotEqual(self.mode("lane-1"), "record")
        self.at(900)
        self.assertEqual(self.m.detail(self.r1)["state"], "done", "relay 1 finishes without them")
        self.m.start(r2, "md")
        self.assertEqual(self.mode("lane-3"), "sighter")
        self.assertEqual(self.lane(r2, "lane-3")["own"], "waiting")
        self.at(910); self.shoot("lane-3")                     # before their stage: flagged
        self.at(1000)                                           # the record stage: 200 s left + 2 min = 320 s, sighters first
        self.assertEqual(self.m.detail(r2)["lanes"][0]["own_remaining_ms"], 320_000)
        self.assertEqual(self.mode("lane-3"), "sighter")
        self.at(1010); self.shoot("lane-3")                    # unlimited sighters (7.18.3)
        self.m.command(r2, "start_record", "ro", device_id="lane-3")
        for t in (1020, 1030, 1040):
            self.at(t); self.shoot("lane-3")                   # two owed, one too many
        whys = [f[3] for f in self.flags(r2)]
        self.assertEqual(whys, ["before this shooter rejoins at record 1", "more than 4 record shots in record 1"])
        self.assertEqual(self.entry("Ames")["shots"], 4, "2 on relay 1 + 2 on relay 2 (the extra waits for a decision)")
        self.assertEqual(self.entry("Ames")["moved_from"], {"relay": 1, "lane": 1})
        self.at(1310)                                           # the match's record stage ended at 1300; theirs at 1320
        self.assertEqual(self.mode("lane-3"), "record")
        self.at(1321)
        self.assertNotEqual(self.mode("lane-3"), "record")
        self.at(1400)                                           # the rest of the match as everyone: changeover 1300, sighting 1360
        self.assertEqual(self.mode("lane-3"), "sighter", "sighting of the next stage with everyone")
        self.at(1461)
        self.assertEqual(self.mode("lane-3"), "record", "record 2 with everyone")

    def test_waits_in_sighting_through_earlier_record_stages(self):
        self.at(600)                                            # relay 1 in its second record stage (phase 4, from 560)
        r2 = self.cc.finish_on_later_relay(self.r1, "lane-1", None, "lane-3", "md")
        self.at(700)
        self.assertNotEqual(self.mode("lane-1"), "record", "the failed target is out of the match")
        self.at(900); self.m.start(r2, "md")
        self.at(1001)                                           # relay 2 in its first record stage: Ames waits for stage 4
        self.assertEqual((self.m.detail(r2)["clock"]["kind"], self.mode("lane-3")), ("record", "sighter"))

    def test_less_time_than_the_relay(self):
        self.at(390)                                            # 10 s left: on relay 2 they get 10 s + 2 min = 130 s
        r2 = self.cc.finish_on_later_relay(self.r1, "lane-1", None, "lane-3", "md")
        self.at(900); self.m.start(r2, "md")
        self.at(1000); self.m.command(r2, "start_record", "ro", device_id="lane-3")
        self.at(1129)
        self.assertEqual(self.mode("lane-3"), "record")
        self.at(1131)
        self.assertEqual(self.mode("lane-3"), "sighter", "their time ended at 1130")
        self.at(1140); self.shoot("lane-3")
        self.assertEqual([f[3] for f in self.flags(r2)], ["after this shooter's own time"])
        self.at(1301)
        self.assertEqual(self.m.detail(r2)["clock"]["phase"], 2, "the relay goes on as usual")

    def test_onto_an_existing_later_relay_and_what_is_refused(self):
        r2 = self.cc.add_relay(self.cid, [{"device_id": "lane-4", "competitor_id": self.cc.save_competitor(self.cid, {"family": "Cole"})}], "md")
        self.at(200)
        with self.assertRaises(MatchError):
            self.cc.finish_on_later_relay(self.r1, "lane-1", r2, "lane-4", "md")      # taken on that relay
        with self.assertRaises(MatchError):
            self.cc.finish_on_later_relay(self.r1, "lane-1", self.r1, "lane-3", "md")  # not a later relay
        with self.assertRaises(MatchError):
            self.cc.finish_on_later_relay(self.r1, "lane-1", r2, "lane-2", "md")      # a target in a match
        self.assertEqual(self.cc.finish_on_later_relay(self.r1, "lane-1", r2, "lane-3", "md"), r2)
        self.assertEqual(sorted(l["competitor"] for l in self.m.detail(r2)["lanes"]), ["AMES", "COLE"])
        with self.assertRaises(MatchError):
            self.cc.finish_on_later_relay(self.r1, "lane-1", None, "lane-3", "md")     # gone already
        self.at(410)                                            # changeover: between stages
        with self.assertRaises(MatchError):
            self.cc.finish_on_later_relay(self.r1, "lane-2", None, "lane-3", "md")
        self.core.handle_heartbeat({"device_id": "lane-5", "boot_count": 1, "uptime_ms": 0})
        solo = self.m.create("Solo", self.custom(FORMAT), [{"device_id": "lane-5"}], "md")
        self.m.start(solo, "md")
        with self.assertRaises(MatchError):
            self.cc.finish_on_later_relay(solo, "lane-5", None, "lane-4", "md")         # not a competition


class LaterRelayOtherBooks(MatchCase):
    """Only the CMP books cover finishing on a later relay: elsewhere it stays available, the match director decides
    (no extra time, no rule cited); the time left is still kept."""
    def test_no_extra_time_without_the_rule(self):
        from fetserver.rulebooks import later_relay
        self.assertEqual([later_relay(b) for b in ("CMP-3PAR", "CMP-AG", "ISSF", "NRA", "NONE")],
                         [(120_000, "CMP 3PAR 7.18, 7.18.3"), (120_000, "CMP AG 7.12, 7.12.2"), (0, None), (0, None), (0, None)])
        self.core.handle_heartbeat({"device_id": "lane-3", "boot_count": 1, "uptime_ms": 0})
        cc = self.core.competitions
        cid = cc.create("League", self.m.save_format(dict(tiny(FORMAT), name="Two stages", book="ISSF"), "md")["id"], "md")
        ames = cc.save_competitor(cid, {"family": "Ames"})
        r1 = cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": ames}], "md")
        self.m.start(r1, "md")
        self.at(200)                                            # 200 s of the record stage left
        r2 = cc.finish_on_later_relay(r1, "lane-1", None, "lane-3", "md")
        self.at(900)
        self.m.start(r2, "md")
        self.at(1000)
        self.assertEqual(self.m.detail(r2)["lanes"][0]["own_remaining_ms"], 200_000, "the time left, nothing added")
        rows = [r for r in self.m.incidents(r1)["rows"] if r["what"] == "Sent to a later relay"]
        self.assertEqual([r["rule"] for r in rows], [""])

