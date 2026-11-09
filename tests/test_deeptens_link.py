"""The Pi's side of the deepTENS link (fetserver/deeptens.py), against a fake deepTENS on a local port: linking by
code, the token file, heartbeats and the icon, and the outbox through outages, resends, restarts and unlinking.
The gate for P1: nothing queued is lost and nothing is stored twice."""
import gzip
import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fetserver.core import Core
from fetserver.db import Database
from fetserver.deeptens import DeepTens, LinkError, check_url
from tests.test_match_http import MatchHttpBase


class FakeDeepTens:
    """Just enough of deepTENS (monitors/api.py there): link, poll, heartbeat, items (keeps the highest rev), unlink."""
    def __init__(self):
        self.approved = self.denied = self.slow = False
        self.token = "dtm_secret-token-123"
        self.linked = True
        self.down = None                 # an HTTP status to answer everything with (503: deepTENS in trouble)
        self.stored = {}                 # key -> (rev, data)
        self.received = []               # every (key, rev) that arrived, in order
        self.heartbeats = []
        self.reject_keys = set()
        self.during_items = None         # called while an items request is being answered (a race with the Pi)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body = json.loads(raw or b"{}")
                st, out = fake.handle(self.path, body, self.headers.get("Authorization", ""))
                data = json.dumps(out).encode()
                self.send_response(st)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def handle(self, path, body, auth):
        if self.down:
            return self.down, {"error": "down"}
        if path == "/api/monitor/v1/link":
            return 200, {"device_code": "dev-code", "user_code": "KQ7-M4P", "verification_uri": self.url + "/link/",
                         "verification_uri_complete": self.url + "/link/?code=KQ7M4P", "expires_in": 900, "interval": 5}
        if path == "/api/monitor/v1/link/poll":
            if self.denied:
                return 200, {"status": "denied"}
            if self.slow:
                return 200, {"status": "slow_down"}
            if not self.approved:
                return 200, {"status": "pending"}
            return 200, {"status": "approved", "token": self.token, "monitor_id": 7, "range": {"id": 3, "name": "Riverside"}}
        if auth != "Bearer " + self.token or not self.linked:
            return 401, {"error": "not linked"}
        if path == "/api/monitor/v1/heartbeat":
            self.heartbeats.append(body)
            return 200, {"ok": True, "lane_conflicts": [2] if any(x["lane"] == 2 for x in body["lanes"]) else []}
        if path == "/api/monitor/v1/unlink":
            self.linked = False
            return 200, {"ok": True}
        if path == "/api/monitor/v1/items":
            if self.during_items:
                self.during_items()
                self.during_items = None
            acks, rejected = [], []
            for it in body["items"]:
                if it["key"] in self.reject_keys:
                    rejected.append({"id": it["id"], "why": "bad data"})
                    continue
                self.received.append((it["key"], it["rev"]))
                if it["rev"] > self.stored.get(it["key"], (0, None))[0]:
                    self.stored[it["key"]] = (it["rev"], it["data"])
                acks.append(it["id"])
            return 200, {"acks": acks, "rejected": rejected}
        return 404, {}


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class LinkCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.core = Core(Database(str(self.dir / "fet.db")))
        for did in ("t1", "t2", "t3"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})
        self.core.set_lane("t3", None)                       # a spare: never sent
        self.fake = FakeDeepTens()
        self.addCleanup(self.fake.close)
        self.clock = Clock()
        self.dt = self.new()

    def new(self):
        return DeepTens(self.core, self.dir, clock=self.clock, name=lambda: "Club Pi", version="0.7.18")

    def link(self):
        self.dt.start_link(self.fake.url)
        self.fake.approved = True
        self.clock.t += 5
        self.dt.step()
        self.assertTrue(self.dt.linked)

    def queue(self, key, **data):
        with self.core.db.write() as c:
            return DeepTens.enqueue(c, "match" if key[0] == "m" else "competition", key, dict({"name": key}, **data), 0)

    def run_until_quiet(self, rounds=20):
        for _ in range(rounds):
            self.clock.t += self.dt.step()


class Addresses(unittest.TestCase):
    def test_https_or_this_machine_only(self):
        self.assertEqual(check_url("https://deeptens.com:8443/"), "https://deeptens.com:8443")
        self.assertEqual(check_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(check_url("http://localhost:8000"), "http://localhost:8000")
        for bad in ("http://deeptens.com", "http://192.168.1.5:8000", "ftp://x", "deeptens.com", "https://x.com/path", "https://u@x.com", ""):
            with self.assertRaises(LinkError, msg=bad):
                check_url(bad)


class Linking(LinkCase):
    def test_link_by_code(self):
        st = self.dt.start_link(self.fake.url)
        self.assertEqual((st["linking"]["user_code"], st["linked"]), ("KQ7-M4P", False))
        self.assertTrue(st["linking"]["uri_complete"].endswith("?code=KQ7M4P"))
        self.clock.t += 5
        self.dt.step()
        self.assertFalse(self.dt.linked, "pending")
        self.fake.approved = True
        self.clock.t += 2
        self.assertGreater(self.dt.step(), 1, "not before the interval")
        self.assertFalse(self.dt.linked)
        self.clock.t += 5
        self.dt.step()
        st = self.dt.status()
        self.assertEqual((st["linked"], st["range"]["name"], st["linking"]), (True, "Riverside", None))
        path = self.dir / "deeptens.json"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, "the token file is private")
        self.assertIn(self.fake.token, path.read_text())
        self.assertNotIn(self.fake.token.encode(), (self.dir / "fet.db").read_bytes(), "never in the database (or its backups)")
        self.assertTrue(self.new().linked, "a restart keeps the link")

    def test_refused_expired_and_already_linked(self):
        self.dt.start_link(self.fake.url)
        self.fake.denied = True
        self.clock.t += 5
        self.dt.step()
        self.assertEqual((self.dt.linked, self.dt.status()["linking"], self.dt.error), (False, None, "the link was refused on deepTENS"))
        self.fake.denied = False
        self.dt.start_link(self.fake.url)
        self.clock.t += 901
        self.dt.step()
        self.assertIn("expired", self.dt.error)
        self.link()
        with self.assertRaises(LinkError):
            self.dt.start_link(self.fake.url)

    def test_slow_down_lengthens_the_wait(self):
        self.dt.start_link(self.fake.url)
        self.fake.slow = True
        self.clock.t += 5
        self.assertEqual(self.dt.step(), 10, "5 s more, as RFC 8628 says")
        self.fake.slow, self.fake.approved = False, True
        self.clock.t += 5
        self.dt.step()
        self.assertFalse(self.dt.linked, "not before the longer wait")
        self.clock.t += 5
        self.dt.step()
        self.assertTrue(self.dt.linked)

    def test_deeptens_unreachable_when_starting(self):
        self.fake.close()
        with self.assertRaisesRegex(LinkError, "could not be reached"):
            self.dt.start_link(self.fake.url)


class Linked(LinkCase):
    def setUp(self):
        super().setUp()
        self.link()

    def test_heartbeat_lanes_and_the_icon(self):
        self.dt.step()
        hb = self.fake.heartbeats[-1]
        self.assertEqual([x["lane"] for x in hb["lanes"]], [1, 2], "spares are not sent")
        self.assertEqual((hb["name"], hb["version"]), ("Club Pi", "0.7.18"))
        st = self.dt.status()
        self.assertEqual((st["connected"], st["lane_conflicts"]), (True, [2]))
        self.fake.down = 503
        self.clock.t += 89
        self.assertTrue(self.dt.status()["connected"], "green for 90 s after the last success")
        self.run_until_quiet(10)
        self.assertFalse(self.dt.status()["connected"], "then red")
        self.assertIn("503", self.dt.status()["error"])

    def test_outbox_survives_an_outage_and_delivers_exactly_once(self):
        self.fake.down = 503
        self.queue("m1")
        self.queue("m2")
        self.queue("m1", note="newer")                       # replaces the waiting m1
        waits = []
        for _ in range(60):
            self.clock.t += self.dt.step()
            if not waits or waits[-1] != self.dt.backoff:
                waits.append(self.dt.backoff)
        self.assertEqual(waits, [2, 4, 8, 16, 32, 60], "the wait doubles, up to a minute")
        self.assertEqual(self.dt.status()["outbox"], 2, "nothing lost, one row per key")
        self.fake.down = None
        self.clock.t += 60
        self.run_until_quiet()
        self.assertEqual(sorted(self.fake.received), [("m1", 2), ("m2", 1)], "each once, the newest m1")
        self.assertEqual(self.fake.stored["m1"][1]["note"], "newer")
        self.assertEqual(self.dt.status()["outbox"], 0)

    def test_a_lost_answer_is_resent_and_harmless(self):
        self.queue("m1")
        real = self.dt.post

        def lose_answer(url, path, body, token=None, compress=False):
            st, out = real(url, path, body, token, compress)       # deepTENS stored it ...
            if path.endswith("/items"):
                raise OSError("connection reset")                 # ... but the answer never arrived
            return st, out
        self.dt.post = lose_answer
        self.dt.step()
        self.assertEqual(self.dt.status()["outbox"], 1, "not acknowledged: kept")
        self.dt.post = real
        self.clock.t += 10
        self.run_until_quiet()
        self.assertEqual(self.fake.received, [("m1", 1), ("m1", 1)], "sent twice ...")
        self.assertEqual(self.fake.stored["m1"][0], 1, "... stored once")
        self.assertEqual(self.dt.status()["outbox"], 0)

    def test_a_newer_snapshot_while_one_is_being_sent(self):
        self.queue("m1", n=1)
        self.fake.during_items = lambda: self.queue("m1", n=2)
        self.dt.step()
        self.assertEqual(self.dt.status()["outbox"], 1, "the ack was for rev 1: rev 2 still waits")
        self.run_until_quiet()
        self.assertEqual(self.fake.stored["m1"][0], 2)

    def test_a_restart_keeps_the_outbox(self):
        self.fake.down = 503
        self.queue("c4")
        self.dt.step()
        self.fake.down = None
        self.dt = self.new()                                  # the Pi restarted
        self.run_until_quiet()
        self.assertEqual(self.fake.received, [("c4", 1)])

    def test_rejected_items_are_kept_and_not_retried(self):
        self.fake.reject_keys = {"m9"}
        self.queue("m9")
        self.queue("m1")
        self.run_until_quiet()
        self.assertEqual(self.dt.status()["rejected"], [{"key": "m9", "why": "bad data"}])
        self.assertEqual(self.fake.received, [("m1", 1)])
        self.assertEqual(self.dt.status()["outbox"], 0, "a rejected item does not count as waiting")

    def test_unlinked_on_deeptens(self):
        self.queue("m1")
        self.fake.linked = False
        self.dt.step()
        st = self.dt.status()
        self.assertEqual((st["linked"], st["unlinked_remotely"], st["error"]), (False, True, "unlinked on deepTENS"))
        self.assertEqual(st["outbox"], 0, "what waited belonged to that link")
        self.assertNotIn(self.fake.token, (self.dir / "deeptens.json").read_text())

    def test_unlink_from_the_pi_even_offline(self):
        self.dt.unlink()
        self.assertFalse(self.fake.linked, "deepTENS told")
        self.link()
        self.fake.close()
        self.dt.unlink()
        self.assertFalse(self.dt.linked, "forgotten even when deepTENS cannot be reached")

    def test_queue_numbers_revisions_per_key(self):
        self.assertEqual([self.queue("m1"), self.queue("m1"), self.queue("m2")], [1, 2, 1])
        self.run_until_quiet()
        self.assertEqual(self.queue("m1"), 3, "after delivery too: deepTENS keeps the highest")


class OverHttp(MatchHttpBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeDeepTens()
        self.addCleanup(self.fake.close)
        self.server.deeptens = DeepTens(self.core, self.dir, name=lambda: "Club Pi", version="0.7.18")

    def test_range_control_links_and_the_icon_is_public(self):
        self.assertEqual(self.req("GET", "/api/v1/deeptens", header=False)[1], {"linked": False, "connected": False}, "public: just the icon's state")
        self.assertEqual(self.req("POST", "/api/v1/admin/deeptens/link", {"url": self.fake.url})[0], 401, "Range Control only")
        st, _, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["admin"] = cookie.split(";")[0]
        st, body, _ = self.req("POST", "/api/v1/admin/deeptens/link", {"url": "http://example.com"}, "admin")
        self.assertEqual((st, "https" in body["error"]), (400, True))
        st, body, _ = self.req("POST", "/api/v1/admin/deeptens/link", {"url": self.fake.url}, "admin")
        self.assertEqual((st, body["linking"]["user_code"]), (200, "KQ7-M4P"))
        self.fake.approved = True
        dt = self.server.deeptens
        dt.next_poll = 0
        dt.step()
        st, body, _ = self.req("GET", "/api/v1/admin/deeptens", None, "admin")
        self.assertEqual((body["linked"], body["range"]["name"]), (True, "Riverside"))
        self.assertNotIn("token", json.dumps(body), "the token never leaves the Pi")
        self.assertEqual(self.req("GET", "/api/v1/deeptens", header=False)[1], {"linked": True, "connected": True})
        st, body, _ = self.req("POST", "/api/v1/admin/deeptens/unlink", {}, "admin")
        self.assertEqual((st, body["linked"], self.fake.linked), (200, False, False))


if __name__ == "__main__":
    unittest.main()
