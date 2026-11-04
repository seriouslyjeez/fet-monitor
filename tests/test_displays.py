"""TV displays (fetserver/displays.py): saved views at a 5-character address that follow the range or a competition
from relay to relay and into its final, read only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from fetserver.displays import ALPHABET, pick
from fetserver.matches import MatchError
from tests.test_matches import MatchCase


class Pick(unittest.TestCase):
    def rows(self, *s):
        return [{"id": i + 1, "state": st, "ended_ms": e} for i, (st, e) in enumerate(s)]

    def test_under_way_then_next_then_last_finished(self):
        self.assertEqual(pick(self.rows(("done", 5), ("running", None), ("ready", None))), [2])
        self.assertEqual(pick(self.rows(("paused", None), ("holding", None), ("overtime", None))), [1, 2, 3], "all under way")
        self.assertEqual(pick(self.rows(("done", 5), ("ready", None), ("ready", None))), [2], "the next relay")
        self.assertEqual(pick(self.rows(("done", 9), ("done", 5))), [1], "the one that finished last")
        self.assertEqual(pick([]), [])


class Displays(MatchCase):
    def setUp(self):
        super().setUp()
        self.d = self.core.displays
        for did in ("lane-3", "lane-4"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})
        self.fid = self.m.save_format({"name": "Mini", "book": "ISSF", "phases": [
            {"kind": "record", "label": "R", "seconds": 100, "shots": 1, "position": "standing"}]}, "md")["id"]

    def test_codes(self):
        code = self.d.save({"name": "Hall TV"})
        self.assertRegex(code, "^[%s]{5}$" % ALPHABET)
        self.assertEqual(self.d.show(code.lower())["view"]["code"], code, "the address is not case sensitive")
        self.assertEqual(self.d.save({"name": "Hall TV", "code": "range"}, code), "RANGE", "an address of your own")
        with self.assertRaisesRegex(MatchError, "no such display"):
            self.d.show(code)                                 # the old address is gone
        with self.assertRaisesRegex(MatchError, "another display has the address RANGE"):
            self.d.save({"name": "Two", "code": "RANGE"})
        for bad in ("RANG", "RANGE1", "RAN-E"):
            with self.assertRaisesRegex(MatchError, "5 letters or digits"):
                self.d.save({"name": "x", "code": bad})
        self.d.delete("range")
        self.assertEqual(self.d.list(), [])

    def test_settings_are_checked(self):
        for data, why in (({"name": ""}, "needs a name"), ({"name": "a", "show": "tv"}, "targets or a scoreboard"),
                          ({"name": "a", "follow": "x"}, "follows the range"), ({"name": "a", "theme": "pink"}, "auto, dark or light"),
                          ({"name": "a", "show": "board", "follow": "none"}, "scoreboard follows"),
                          ({"name": "a", "follow": "competition", "competition_id": 99}, "choose the competition"),
                          ({"name": "a", "follow": "match", "match_id": True}, "choose the match"),
                          ({"name": "a", "targets": "lane-1"}, "list of target ids")):
            with self.assertRaisesRegex(MatchError, why):
                self.d.save(data)
        code = self.d.save({"name": "Board", "show": "board", "targets": ["lane-1"]})
        self.assertEqual(self.d.show(code)["view"]["targets"], [], "a scoreboard has no target list")

    def competition(self):
        cc = self.core.competitions
        cid = cc.create("Club match", self.fid, "md")
        people = [cc.save_competitor(cid, {"family": f}) for f in ("Ames", "Bell", "Cole", "Dunn")]
        r1 = cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": people[0]}, {"device_id": "lane-2", "competitor_id": people[1]}], "md")
        r2 = cc.add_relay(cid, [{"device_id": "lane-3", "competitor_id": people[2]}, {"device_id": "lane-4", "competitor_id": people[3]}], "md")
        return cc, cid, r1, r2

    def test_a_competition_from_relay_to_relay_and_into_its_final(self):
        cc, cid, r1, r2 = self.competition()
        tv = self.d.save({"name": "Hall", "follow": "competition", "competition_id": cid})
        board = self.d.save({"name": "Board", "show": "board", "follow": "competition", "competition_id": cid})
        now = lambda code: self.d.show(code)["now"]
        self.assertEqual(now(tv), [r1], "before the start: the first relay")
        self.m.start(r1, "md")
        self.at(10)
        self.shoot("lane-1"); self.shoot("lane-2")
        self.assertEqual(now(tv), [r1])
        s = self.d.show(board)
        self.assertEqual(([c["id"] for c in s["competitions"]], s["competitions"][0]["current_relay"]), ([cid], r1),
                         "the scoreboard: all relays together, with the clock of the one on now")
        self.at(200)                                          # relay 1 is over
        self.assertEqual(self.m.detail(r1)["state"], "done")
        self.assertEqual(now(tv), [r2], "relay 2 is next")
        self.m.start(r2, "md")
        self.at(210)
        self.shoot("lane-3"); self.shoot("lane-4")
        self.at(400)
        self.assertEqual(now(tv), [r2], "after the last relay: its results stay up")
        fin = cc.create_final(cid, "issf", ["lane-1", "lane-2"], "md")
        self.assertEqual(now(tv), [fin], "the final, as soon as it is set up")
        s = self.d.show(board)
        self.assertEqual((s["now"], s["competitions"], s["matches"][0]["relay_of"]), ([fin], [], None), "a final has its own board")
        self.assertIn("final", s["matches"][0])
        self.assertEqual(sorted(r["device_id"] for r in self.d.show(tv)["matches"][0]["rows"]), ["lane-1", "lane-2"],
                         "the targets view shows the finalists' targets")
        self.assertEqual([v["now"][0]["id"] for v in self.d.list()], [fin, fin])

    def test_an_aborted_relay_is_skipped(self):
        cc, cid, r1, r2 = self.competition()
        tv = self.d.save({"name": "Hall", "follow": "competition", "competition_id": cid})
        self.m.start(r1, "md")
        self.m.command(r1, "abort", "md")
        self.assertEqual(self.d.show(tv)["now"], [r2])

    def test_the_range_forgets_a_match_after_6_hours(self):
        single = self.m.create("Evening", self.fid, [{"device_id": "lane-1"}], "md")
        self.m.start(single, "md")
        self.at(200)
        tv = self.d.save({"name": "Range"})
        self.assertEqual(self.d.show(tv)["now"], [single], "finished: its results stay up")
        self.at(200 + 6 * 3600 + 1)
        self.assertEqual(self.d.show(tv)["now"], [], "6 h later: nothing")
        self.assertEqual(self.d.show(self.d.save({"name": "M", "follow": "match", "match_id": single}))["now"], [single], "one match: always")

    def test_saving_under_its_own_address_and_telling_the_tvs(self):
        code = self.d.save({"name": "Hall", "code": "HALL1"})
        q = self.core.hub.subscribe()
        self.assertEqual(self.d.save({"name": "Hall 2", "code": "hall1"}, code), "HALL1", "its own address is not another display's")
        self.d.save({"name": "Hall 2", "code": "HALL2"}, code)
        seen = []
        while not q.empty():
            seen.append(q.get_nowait().get("code"))
        self.assertEqual(seen, ["HALL1", "HALL1", "HALL2"], "a TV on the old address hears of the change")

    def test_following_the_range(self):
        cc, cid, r1, r2 = self.competition()
        self.core.handle_heartbeat({"device_id": "lane-5", "boot_count": 1, "uptime_ms": 0})
        single = self.m.create("Practice match", self.fid, [{"device_id": "lane-5"}], "md")
        tv = self.d.save({"name": "Range"})
        self.assertEqual(self.d.show(tv)["now"], [r1], "nothing under way: the first to start")
        self.m.start(single, "md")
        self.assertEqual(self.d.show(tv)["now"], [single])
        self.m.start(r1, "md")
        self.assertEqual(self.d.show(tv)["now"], [r1, single], "everything under way")
        self.m.command(single, "abort", "md")
        self.assertEqual(self.d.show(tv)["now"], [r1], "an aborted match is never shown")

    def test_one_match_and_hidden_results(self):
        cc, cid, r1, r2 = self.competition()
        tv = self.d.save({"name": "Relay 2", "follow": "match", "match_id": r2})
        self.assertEqual(self.d.show(tv)["now"], [r2], "one match, whatever else is on")
        with self.core.db.write() as c:                       # results deleted (resultstore): off every display
            c.execute("UPDATE competitions SET results_hidden=1 WHERE id=?", (cid,))
        comp = self.d.save({"name": "Comp", "follow": "competition", "competition_id": cid})
        self.assertEqual((self.d.show(comp)["now"], self.d.show(self.d.save({"name": "R"}))["now"]), ([], []))

    def test_targets_only(self):
        tv = self.d.save({"name": "Practice", "follow": "none", "targets": ["lane-2", "lane-1", "lane-2"]})
        s = self.d.show(tv)
        self.assertEqual((s["now"], s["matches"], s["view"]["targets"]), ([], [], ["lane-2", "lane-1"]))


class Http(unittest.TestCase):
    """The address serves the page; the data is public and read only; saving needs Range Control."""

    def setUp(self):
        from fetserver.admin import Admin
        from fetserver.core import Core
        from fetserver.db import Database
        from fetserver.server import make_server
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = Core(Database(os.path.join(tmp.name, "t.db")))
        self.srv = make_server(self.core, "tok", "127.0.0.1", 0, admin=Admin(tmp.name))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def req(self, path, body=None, headers=None):
        r = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                   headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_routes(self):
        code = self.core.displays.save({"name": "Hall"})
        st, body = self.req("/" + code.lower())
        self.assertEqual((st, b"<title>FeT Monitor</title>" in body), (200, True), "the address serves the page")
        self.assertEqual(self.req("/" + code + "/")[0], 200)
        self.assertEqual(self.req("/ABCDEF")[0], 404, "only 5 characters")
        st, body = self.req("/api/v1/displays/" + code)
        self.assertEqual((st, json.loads(body)["view"]["name"]), (200, "Hall"))
        self.assertEqual(self.req("/api/v1/displays/ZZZZZ")[0], 404)
        self.assertEqual(self.req("/api/v1/admin/displays", {"name": "X"}, {"X-FET-Admin": "1"})[0], 401, "saving needs Range Control")
        self.assertEqual(self.req("/api/v1/admin/displays/" + code + "/delete", {}, {"X-FET-Admin": "1"})[0], 401)


if __name__ == "__main__":
    unittest.main()
