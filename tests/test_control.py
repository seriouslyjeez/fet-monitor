import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from fetserver.core import BadRequest, Core, Forbidden, NotFound
from fetserver.db import Database
from fetserver.server import make_server

ALICE = "alice-client-01"
BOB = "bob-client-0002"
SHOT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":%s , "N":1, "E":2, "S":3, "W":0 , '
        '"V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }')
MISS = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'
BASE = 1_000_000_000


class Clock:
    def __init__(self):
        self.t = BASE

    def __call__(self):
        return self.t


class CtlCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.core = Core(Database(os.path.join(self.tmp.name, "t.db")), now_ms=self.clock, lease_ms=60_000)
        self.core.handle_heartbeat({"device_id": "a1", "boot_count": 1, "uptime_ms": 0, "name": "Lane 1"})
        self.seq = 0

    def at(self, ms):
        self.clock.t = BASE + ms

    def shoot(self, at_ms, x="0.00", y="0.00", raw=None, deliver_at=None):
        """A shot detected at `at_ms` after boot, delivered at `deliver_at` (default: immediately)."""
        self.seq += 1
        deliver_at = at_ms if deliver_at is None else deliver_at
        self.at(deliver_at)
        raw = raw or SHOT % (self.seq, x, y)
        self.core.handle_events({"device_id": "a1", "boot_count": 1, "uptime_ms": deliver_at, "events": [
            {"seq": self.seq, "type": "shot", "uptime_ms": at_ms, "raw": raw}]})

    def target(self, client=None):
        return self.core.list_targets(client)[0]


class Lease(CtlCase):
    def test_claim_conflict_takeover_expiry_release(self):
        r = self.core.acquire_control("a1", ALICE, "Alice")
        self.assertTrue(r["granted"])
        self.assertTrue(r["mine"])
        r = self.core.acquire_control("a1", BOB, "Bob")
        self.assertEqual((r["granted"], r["held_by"], r["mine"]), (False, "Alice", False))
        self.assertTrue(self.core.acquire_control("a1", ALICE, "Alice")["granted"])        # renew
        r = self.core.acquire_control("a1", BOB, "Bob", takeover=True)
        self.assertEqual((r["granted"], r["held_by"]), (True, "Bob"))
        self.assertFalse(self.core.acquire_control("a1", ALICE, "Alice")["granted"])
        self.at(61_000)                                                                   # Bob never renewed
        self.assertIsNone(self.target()["lease"]["held_by"])
        self.assertTrue(self.core.acquire_control("a1", ALICE, "Alice")["granted"])
        self.assertTrue(self.core.release_control("a1", ALICE))
        self.assertFalse(self.core.release_control("a1", ALICE))
        self.assertIsNone(self.target()["lease"]["held_by"])

    def test_release_only_by_holder(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.assertFalse(self.core.release_control("a1", BOB))
        self.assertEqual(self.target(ALICE)["lease"]["held_by"], "Alice")

    def test_mine_flag_depends_on_who_asks(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.assertTrue(self.target(ALICE)["lease"]["mine"])
        self.assertFalse(self.target(BOB)["lease"]["mine"])
        self.assertFalse(self.target()["lease"]["mine"])

    def test_validation(self):
        with self.assertRaises(NotFound):
            self.core.acquire_control("nobody", ALICE, "Alice")
        with self.assertRaises(BadRequest):
            self.core.acquire_control("a1", "short", "Alice")
        with self.assertRaises(BadRequest):
            self.core.acquire_control("a1", ALICE, "   ")


class Modes(CtlCase):
    def test_mode_needs_the_claim(self):
        with self.assertRaises(Forbidden):
            self.core.set_mode("a1", ALICE, "record")
        self.core.acquire_control("a1", ALICE, "Alice")
        with self.assertRaises(Forbidden):
            self.core.set_mode("a1", BOB, "record")
        self.at(61_000)
        with self.assertRaises(Forbidden):                                                # claim expired
            self.core.set_mode("a1", ALICE, "record")

    def test_invalid_mode(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        with self.assertRaises(BadRequest):
            self.core.set_mode("a1", ALICE, "warmup")

    def test_defaults_idempotence_and_new_series(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        a = self.core.set_mode("a1", ALICE, "sighter")
        self.assertEqual((a["shooter"], a["kind"]), ("Alice \u2013 Match", "match"))   # session: who is in control + kind
        self.assertEqual(self.core.set_mode("a1", ALICE, "sighter")["segment_id"], a["segment_id"])
        r = self.core.set_mode("a1", ALICE, "record")                                  # same match, new segment
        self.assertEqual(r["series_id"], a["series_id"])
        self.assertNotEqual(r["segment_id"], a["segment_id"])
        n = self.core.set_mode("a1", ALICE, "record", new_series=True)
        self.assertNotEqual(n["series_id"], a["series_id"])
        c = self.core.set_mode("a1", ALICE, "record", shooter="Carol")                 # a different shooter: fresh series
        self.assertEqual(c["shooter"], "Carol")
        self.assertNotEqual(c["series_id"], n["series_id"])
        p = self.core.set_mode("a1", ALICE, "practice")
        self.assertEqual((p["shooter"], p["kind"]), ("Carol", "practice"))             # kept until changed
        self.assertNotEqual(p["series_id"], c["series_id"])
        segs = self.core.list_segments("a1")
        self.assertEqual([g["mode"] for g in segs], ["practice", "record", "record", "record", "sighter"])
        self.assertEqual([g["open"] for g in segs], [True, False, False, False, False])
        kinds = [(g["kind"], g["open"]) for g in self.core.list_series("a1")]
        self.assertEqual(kinds, [("practice", True), ("match", False), ("match", False), ("match", False)])

    def test_default_mode_is_practice_with_no_series(self):
        t = self.target()
        self.assertEqual((t["mode"], t["series_id"], t["shooter"], t["kind"]), ("practice", None, None, "practice"))
        self.assertEqual((t["view"]["count"], t["view"]["total"], t["view"]["bull_size"]), (0, 20, 10))

    def test_shot_takes_the_mode_in_force_when_it_was_fired(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.at(10_000)
        self.core.set_mode("a1", ALICE, "record")
        self.shoot(12_000, "0.00", "0.00")                                               # record, 10.9
        self.at(30_000)
        self.core.set_mode("a1", ALICE, "sighter")
        # fired at 20 s (record) but only delivered at 45 s, after the switch to sighter
        self.shoot(20_000, "2.00", "0.00", deliver_at=45_000)
        self.shoot(35_000, "0.00", "3.00")                                               # sighter
        by_shot = {s["shot_number"]: s for s in self.core.list_shots("a1")}
        self.assertEqual([by_shot[i]["stream"] for i in (1, 2, 3)], ["scored", "scored", "sighter"])
        self.assertEqual([by_shot[i]["series_no"] for i in (1, 2, 3)], [1, 2, 1])
        t = self.target()
        self.assertEqual((t["mode"], t["view"]["stream"], t["view"]["count"]), ("sighter", "sighter", 1))
        rec = self.core.series_view(t["series_id"], "scored")
        self.assertEqual((rec["agg"]["shots"], rec["agg"]["decimal"], rec["agg"]["integer"]), (2, 21.1, 20))  # 10.9 + 10.2

    def test_shot_lag_moves_shots_earlier(self):
        self.core.shot_lag_ms = 3000
        self.core.acquire_control("a1", ALICE, "Alice")
        self.at(10_000)
        self.core.set_mode("a1", ALICE, "record")
        self.at(11_000)
        self.core.set_mode("a1", ALICE, "sighter")
        self.shoot(12_000, deliver_at=12_000)         # printed at 12 s, really fired at 9 s -> before record began
        self.assertIsNone(self.core.list_shots("a1")[0]["segment_id"])
        self.assertIsNone(self.core.list_shots("a1")[0]["series_no"])

    def test_totals_misses_and_filters(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.at(1_000)
        self.core.set_mode("a1", ALICE, "record")
        for i in range(12):
            self.shoot(2_000 + i * 100, "0.00", "0.00")
        self.shoot(5_000, raw=MISS % 99)
        t = self.target(ALICE)
        v = t["view"]
        self.assertEqual((v["count"], v["total"], v["agg"]["decimal"], v["agg"]["integer"], v["agg"]["misses"], v["agg"]["x"]),
                         (13, 60, 130.8, 120, 1, 12))
        self.assertEqual(len(v["bull_shots"]), 3)                    # shots 11..13 of the second bull
        self.assertTrue(v["last_shot"]["miss"])
        self.assertIsNone(v["last_shot"]["x_mm"])
        self.at(6_000)
        self.core.set_mode("a1", ALICE, "practice")
        self.shoot(7_000)
        first = self.core.list_series("a1")[1]["id"]
        self.assertEqual(len(self.core.list_shots("a1", segment_id=self.core.list_segments("a1")[1]["id"])), 13)
        self.assertEqual(len(self.core.list_shots("a1")), 14)
        self.assertEqual(self.core.series_view(first)["agg"]["shots"], 13)

    def test_changes_are_announced(self):
        q = self.core.hub.subscribe()
        self.core.acquire_control("a1", ALICE, "Alice")
        self.core.set_mode("a1", ALICE, "record")
        self.shoot(5_000)
        self.core.release_control("a1", ALICE)
        got = []
        while not q.empty():
            got.append(q.get_nowait()["type"])
        self.assertEqual(got, ["lease", "mode", "shot", "lease"])


class HttpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = Core(Database(os.path.join(self.tmp.name, "t.db")))
        self.server = make_server(self.core, "tok", "127.0.0.1", 0)
        self.server.stream_keepalive_s = 0.2
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        self.core.handle_heartbeat({"device_id": "a1", "boot_count": 1, "uptime_ms": 1, "name": "Lane 1"})

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class Http(HttpCase):
    def test_control_and_mode_over_http(self):
        p = "/api/v1/targets/a1"
        self.assertEqual(self.call("POST", p + "/mode", {"client_id": ALICE, "mode": "record"})[0], 403)
        s, b = self.call("POST", p + "/control", {"client_id": ALICE, "name": "Alice"})
        self.assertEqual((s, b["granted"]), (200, True))
        s, b = self.call("POST", p + "/control", {"client_id": BOB, "name": "Bob"})
        self.assertEqual((s, b["granted"], b["held_by"]), (409, False, "Alice"))
        self.assertEqual(self.call("POST", p + "/control", {"client_id": BOB, "name": "Bob", "takeover": True})[0], 200)
        self.assertEqual(self.call("POST", p + "/mode", {"client_id": ALICE, "mode": "record"})[0], 403)
        s, b = self.call("POST", p + "/mode", {"client_id": BOB, "mode": "record", "shooter": "Carol"})
        self.assertEqual((s, b["mode"], b["shooter"]), (200, "record", "Carol"))
        self.assertEqual(self.call("POST", p + "/mode", {"client_id": BOB, "mode": "nope"})[0], 400)
        s, b = self.call("GET", f"/api/v1/targets?client_id={BOB}")
        t = b["targets"][0]
        self.assertEqual((t["mode"], t["shooter"], t["lease"]["mine"], t["lease"]["held_by"]), ("record", "Carol", True, "Bob"))
        s, b = self.call("GET", p + "/segments")
        self.assertEqual((s, b["segments"][0]["mode"], b["segments"][0]["open"]), (200, "record", True))
        self.assertEqual(self.call("POST", p + "/control/release", {"client_id": BOB})[1], {"released": True})
        self.assertEqual(self.call("POST", "/api/v1/targets/ghost/control", {"client_id": ALICE, "name": "A"})[0], 404)
        self.assertEqual(self.call("GET", p + "/shots?segment_id=abc")[0], 400)

    def test_series_settings_and_review_over_http(self):
        p = "/api/v1/targets/a1"
        self.assertEqual(self.call("POST", p + "/settings", {"client_id": ALICE, "bull_size": 5})[0], 403)
        self.call("POST", p + "/control", {"client_id": ALICE, "name": "Alice"})
        s, b = self.call("POST", p + "/settings", {"client_id": ALICE, "bull_size": 5, "total_practice": 8})
        self.assertEqual((s, b["bull_size"], b["total_practice"], b["group_size"]), (200, 5, 8, 10))
        self.assertEqual(self.call("POST", p + "/settings", {"client_id": ALICE, "bull_size": 0})[0], 400)
        self.assertEqual(self.call("POST", p + "/settings", {"client_id": ALICE, "bull_size": "5"})[0], 400)
        s, b = self.call("POST", p + "/mode", {"client_id": ALICE, "mode": "sighter"})
        self.assertEqual((s, b["kind"]), (200, "match"))
        self.call("POST", p + "/mode", {"client_id": ALICE, "mode": "record"})
        for i in range(7):
            self.core.handle_events({"device_id": "a1", "boot_count": 1, "uptime_ms": 60_000 + i, "events": [
                {"seq": i + 1, "type": "shot", "uptime_ms": 60_000 + i, "raw": SHOT % (i + 1, "2.00", "0.00")}]})
        s, b = self.call("GET", f"/api/v1/targets?client_id={ALICE}")
        v = b["targets"][0]["view"]
        self.assertEqual((v["count"], v["bull_size"], v["bull_index"], [x["n"] for x in v["bull_shots"]], v["agg"]["x"]),
                         (7, 5, 1, [6, 7], 7))
        s, b = self.call("GET", p + "/series")
        sid = b["series"][0]["id"]
        self.assertEqual((s, b["series"][0]["scored"]["shots"], b["series"][0]["sighter"]["shots"]), (200, 7, 0))
        s, b = self.call("GET", f"/api/v1/series/{sid}?stream=scored&bull=0")
        self.assertEqual((s, [x["n"] for x in b["bull_shots"]], b["bull_count"]), (200, [1, 2, 3, 4, 5], 2))
        self.assertEqual(self.call("GET", f"/api/v1/series/{sid}?stream=bogus")[0], 400)
        self.assertEqual(self.call("GET", f"/api/v1/series/{sid}?bull=x")[0], 400)
        self.assertEqual(self.call("GET", "/api/v1/series/99999")[0], 404)
        s, b = self.call("POST", p + "/mode", {"client_id": ALICE, "mode": "record", "new_series": True})
        self.assertNotEqual(b["series_id"], sid)

    def test_dashboard_page_is_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers["Content-Type"])

    def test_live_stream_announces_a_shot(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/v1/stream")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        self.assertEqual(resp.readline().strip(), b": connected")
        self.core.handle_events({"device_id": "a1", "boot_count": 1, "uptime_ms": 10, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": 5, "raw": SHOT % (1, "1.00", "1.00")}]})
        message = None
        for _ in range(20):
            line = resp.readline().strip()
            if line.startswith(b"data:"):
                message = json.loads(line[5:])
                break
        resp.close()                                          # a real browser tab closing drops the socket
        conn.close()
        self.assertEqual(message, {"type": "shot", "device_id": "a1"})
        for _ in range(50):                                   # the handler thread must clean up its subscription
            if self.core.hub.subscriber_count() == 0:
                break
            threading.Event().wait(0.1)
        self.assertEqual(self.core.hub.subscriber_count(), 0)


if __name__ == "__main__":
    unittest.main()


class SessionNames(CtlCase):
    """The session name defaults to who took control and the kind; a new holder never shoots into another's practice."""
    D = "–"

    def series(self):
        return [(g["kind"], g["shooter"], g["open"]) for g in self.core.list_series("a1")]

    def test_defaults_follow_the_kind_and_custom_names_carry(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.assertEqual(self.core.set_mode("a1", ALICE, "practice")["shooter"], f"Alice {self.D} Practice")
        self.assertEqual(self.core.set_mode("a1", ALICE, "sighter")["shooter"], f"Alice {self.D} Match")
        self.assertEqual(self.core.set_mode("a1", ALICE, "practice", new_series=True)["shooter"], f"Alice {self.D} Practice")
        self.assertEqual(self.core.set_mode("a1", ALICE, "practice", shooter="Morning drills")["shooter"], "Morning drills")
        self.assertEqual(self.core.set_mode("a1", ALICE, "sighter")["shooter"], "Morning drills")
        self.assertEqual(self.core.set_mode("a1", ALICE, "ammo")["shooter"], "Alice", "an ammo test keeps the real shooter")
        self.assertEqual(self.core.set_mode("a1", ALICE, "practice")["shooter"], f"Alice {self.D} Practice")

    def test_new_holder_gets_a_fresh_practice_series(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        first = self.core.set_mode("a1", ALICE, "practice")["series_id"]
        self.at(5_000)
        self.shoot(5_000)
        self.core.acquire_control("a1", BOB, "Bob", takeover=True)
        self.assertEqual(self.series(), [("practice", f"Bob {self.D} Practice", True), ("practice", f"Alice {self.D} Practice", False)])
        self.assertNotEqual(self.target(BOB)["series_id"], first)
        for _ in range(3):                                   # renewals do nothing
            self.core.acquire_control("a1", BOB, "Bob")
        self.assertEqual(len(self.series()), 2)
        self.core.acquire_control("a1", ALICE, "Alice", takeover=True)    # Bob's series is empty: renamed, not replaced
        self.assertEqual(self.series(), [("practice", f"Alice {self.D} Practice", True), ("practice", f"Alice {self.D} Practice", False)])
        self.core.release_control("a1", ALICE)
        self.core.acquire_control("a1", ALICE, "Alice")                 # her own default name: kept
        self.assertEqual(len(self.series()), 2)

    def test_custom_names_matches_and_ammo_are_left_alone(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.core.set_mode("a1", ALICE, "practice", shooter="Club night")
        self.at(5_000)
        self.shoot(5_000)
        self.core.acquire_control("a1", BOB, "Bob", takeover=True)
        self.assertEqual(self.series(), [("practice", "Club night", True)])
        self.core.set_mode("a1", BOB, "sighter")
        self.at(6_000)
        self.shoot(6_000)
        self.core.acquire_control("a1", ALICE, "Alice", takeover=True)
        self.assertEqual(self.series()[0], ("match", "Club night", True))
        self.core.set_mode("a1", ALICE, "sighter", shooter=f"Alice {self.D} Match")
        self.core.acquire_control("a1", BOB, "Bob", takeover=True)
        self.assertEqual(self.series()[0], ("match", f"Alice {self.D} Match", True), "a match is never split")
