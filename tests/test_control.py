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
        self.assertEqual(a["shooter"], "Alice")                                          # defaults to the person in control
        self.assertEqual(self.core.set_mode("a1", ALICE, "sighter")["segment_id"], a["segment_id"])
        b = self.core.set_mode("a1", ALICE, "sighter", new_segment=True)
        self.assertNotEqual(b["segment_id"], a["segment_id"])
        c = self.core.set_mode("a1", ALICE, "record", shooter="Carol")
        self.assertEqual(c["shooter"], "Carol")
        self.assertEqual(self.core.set_mode("a1", ALICE, "practice")["shooter"], "Carol")   # kept until changed
        segs = self.core.list_segments("a1")
        self.assertEqual([g["mode"] for g in segs], ["practice", "record", "sighter", "sighter"])
        self.assertEqual([g["open"] for g in segs], [True, False, False, False])

    def test_default_mode_is_practice_with_no_segment(self):
        t = self.target()
        self.assertEqual((t["mode"], t["segment_id"], t["shooter"]), ("practice", None, None))

    def test_shot_takes_the_mode_in_force_when_it_was_fired(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.shoot(5_000)                                                                # before any mode: unassigned
        self.at(10_000)
        self.core.set_mode("a1", ALICE, "record")
        self.shoot(12_000, "0.00", "0.00")                                               # record, 10.9
        self.at(30_000)
        self.core.set_mode("a1", ALICE, "sighter")
        # fired at 20 s (record) but only delivered at 45 s, after the switch to sighter
        self.shoot(20_000, "2.00", "0.00", deliver_at=45_000)
        self.shoot(35_000, "0.00", "3.00")                                               # sighter
        by_shot = {s["shot_number"]: s for s in self.core.list_shots("a1")}
        self.assertEqual([by_shot[i]["mode"] for i in (1, 2, 3, 4)], ["practice", "record", "record", "sighter"])
        self.assertIsNone(by_shot[1]["segment_id"])
        segs = {g["mode"]: g for g in self.core.list_segments("a1")}
        self.assertEqual((segs["record"]["shots"], segs["record"]["total_decimal"], segs["record"]["total_integer"]), (2, 21.1, 20))   # 10.9 + 10.2
        self.assertEqual(segs["sighter"]["shots"], 1)

    def test_shot_lag_moves_shots_earlier(self):
        self.core.shot_lag_ms = 3000
        self.core.acquire_control("a1", ALICE, "Alice")
        self.at(10_000)
        self.core.set_mode("a1", ALICE, "record")
        self.at(11_000)
        self.core.set_mode("a1", ALICE, "sighter")
        self.shoot(12_000, deliver_at=12_000)         # printed at 12 s, really fired at 9 s -> before record began
        self.assertIsNone(self.core.list_shots("a1")[0]["segment_id"])

    def test_totals_misses_recent_and_filters(self):
        self.core.acquire_control("a1", ALICE, "Alice")
        self.at(1_000)
        self.core.set_mode("a1", ALICE, "record")
        for i in range(12):
            self.shoot(2_000 + i * 100, "0.00", "0.00")
        self.shoot(5_000, raw=MISS % 99)
        t = self.target(ALICE)
        self.assertEqual(t["segment"], {"shots": 13, "misses": 1, "total_decimal": 130.8, "total_integer": 120})
        self.assertEqual(len(t["recent_shots"]), 10)
        self.assertTrue(t["last_shot"]["miss"])
        self.assertIsNone(t["last_shot"]["x_mm"])
        self.at(6_000)
        self.core.set_mode("a1", ALICE, "practice")
        self.shoot(7_000)
        first = self.core.list_segments("a1")[1]["id"]
        self.assertEqual(len(self.core.list_shots("a1", segment_id=first)), 13)
        self.assertEqual(len(self.core.list_shots("a1")), 14)

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
