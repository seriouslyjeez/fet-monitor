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
from fetserver.matches import MatchError
from tests.test_match_http import MatchHttpBase
from tests.test_matches import MatchCase


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


class Publishing(MatchCase):
    """What goes to deepTENS as a match runs, and the Match Director's choice to publish (user, 2026-11-08: asked when
    the match is created; published = public at once and live shot by shot, provisional until official; if not, asked
    again when its results are made official; a Publish button on every unpublished match)."""
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fake = FakeDeepTens()
        self.addCleanup(self.fake.close)
        self.dt = DeepTens(self.core, tmp.name, clock=lambda: self.clock.t / 1000, name=lambda: "Club Pi")
        self.core.deeptens = self.dt
        self.fid = self.custom([("sight", 30, 0), ("record", 60, 2)])

    def link(self):
        self.dt.start_link(self.fake.url)
        self.fake.approved = True
        self.dt.next_poll = 0
        self.dt.step()
        self.assertTrue(self.dt.linked)

    def deliver(self):
        for _ in range(10):
            self.dt.next_hb = self.dt.next_send = 0
            self.dt.step()

    def sent(self, key):
        return self.fake.stored.get(key, (0, None))

    def shots_sent(self, key):
        return self.sent(key)[1]["classes"][0]["individuals"][0]["shots"]

    def make(self, publish):
        return self.m.create("Night", self.fid, [{"device_id": "lane-1", "competitor": "Jane Smith"}], "md", publish=publish)

    def test_the_choice_is_required_while_linked(self):
        first = self.make(None)                             # not linked: no question
        self.core.results.delete_matches([first], [], "md")
        self.link()
        with self.assertRaisesRegex(MatchError, "publish this live on deepTENS"):
            self.make(None)
        with self.assertRaisesRegex(MatchError, "publish this live on deepTENS"):
            self.core.competitions.create("League", self.fid, "md")
        no = self.make(False)
        self.assertEqual(self.m.detail(no)["publish_deeptens"], 0)
        self.core.results.delete_matches([no], [], "md")
        self.assertEqual(self.m.detail(self.make(True))["publish_deeptens"], 1)
        self.assertEqual(self.core.competitions.detail(self.core.competitions.create("Cup", self.fid, "md", publish=True))["publish_deeptens"], 1)

    def test_published_at_creation_goes_live_shot_by_shot(self):
        self.link()
        mid = self.make(True)
        self.deliver()
        self.assertEqual(self.fake.stored, {}, "not before it starts")
        self.m.start(mid, "md")
        self.shoot("lane-1")                                # a sighter: never public
        self.at(31)                                         # record
        self.shoot("lane-1")
        self.deliver()
        rev, data = self.sent(f"m{mid}")
        self.assertEqual((data["publish"], data["official"], data["state"]), (True, False, "running"), "public at once, provisional")
        self.assertEqual(self.shots_sent(f"m{mid}"), 1, "the record shot only, not the sighter")
        self.assertTrue(all(x.get("stream", "scored") == "scored" for x in data["classes"][0]["individuals"][0]["shot_list"]))
        self.shoot("lane-1")
        self.assertTrue(self.dt.dirty, "a shot marks the match changed")
        self.deliver()
        self.assertEqual(self.shots_sent(f"m{mid}"), 2, "score by score")
        self.at(91)
        self.m.post_results(mid, "md")
        self.m.make_official(mid, "md", override=True)
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1]["official"], True)
        self.m.set_publish(mid, False, "md")
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1]["publish"], False, "unpublished")

    def test_not_published_goes_up_privately_and_can_be_published_later(self):
        self.link()
        mid = self.make(False)
        self.m.start(mid, "md")
        self.at(31)
        self.shoot("lane-1")
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1]["publish"], False, "private to the range")
        self.m.set_publish(mid, True, "md")
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1]["publish"], True)

    def test_snapshots_are_made_in_the_link_thread(self):
        self.link()
        mid = self.make(False)
        self.m.start(mid, "md")
        self.assertEqual(self.dt.outbox_count(), 0, "the match code only marks it changed")
        self.assertTrue(self.dt.dirty)
        self.deliver()
        self.assertFalse(self.dt.dirty)

    def test_deleted_results_and_deleted_matches(self):
        self.link()
        mid = self.make(True)
        self.m.start(mid, "md")
        self.at(91)
        self.deliver()
        self.core.results.delete_results([f"m{mid}"], "md")
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1]["publish"], False, "results deleted on the Pi: not public")
        self.core.results.restore_results([f"m{mid}"], "md")
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1]["publish"], True, "shown again")
        self.core.results.delete_matches([mid], [], "md")
        self.deliver()
        self.assertEqual(self.sent(f"m{mid}")[1], {"deleted": True, "publish": False, "official": False, "classes": []})
        self.dt.changed("m", 99999)
        self.deliver()
        self.assertNotIn("m99999", self.fake.stored, "never sent: nothing to delete")

    def test_a_relay_is_sent_live_with_its_competition(self):
        self.link()
        cc = self.core.competitions
        cid = cc.create("League", self.fid, "md", publish=False)
        a = cc.save_competitor(cid, {"family": "Smith", "given": "Jane"})
        rid = cc.add_relay(cid, [{"device_id": "lane-1", "competitor_id": a}], "md")
        self.assertIsNone(self.m.detail(rid)["publish_deeptens"], "a relay is not asked: its competition was")
        with self.assertRaisesRegex(MatchError, "competition"):
            self.m.set_publish(rid, True, "md")
        cc.set_publish(cid, True, "md")
        self.assertEqual(self.m.detail(rid)["competition_publish"], 1)
        self.deliver()
        self.assertEqual(self.fake.stored, {}, "no relay started yet")
        self.m.start(rid, "md")
        self.at(31)
        self.shoot("lane-1")
        self.deliver()
        self.assertNotIn(f"m{rid}", self.fake.stored)
        data = self.sent(f"c{cid}")[1]
        self.assertEqual((data["publish"], data["official"]), (True, False), "live, provisional")
        self.assertEqual(self.shots_sent(f"c{cid}"), 1, "shot by shot in a relay too")


class PublishOverHttp(MatchHttpBase):
    def test_only_the_match_director_publishes(self):
        fid = self.core.matches.save_format({"name": "T", "book": "ISSF", "phases": [{"kind": "record", "label": "R", "seconds": 60, "shots": 1}]}, "md")["id"]
        mid = self.core.matches.create("M", fid, [{"device_id": "lane-1"}], "md")
        for who, pw in (("ro", "officer-pass"), ("md", "director-pass")):
            st, _, cookie = self.req("POST", "/api/v1/match/login", {"username": who, "password": pw})
            self.cookies[who] = cookie.split(";")[0]
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/publish", {"publish": True}, "ro")[0], 403)
        st, body, _ = self.req("POST", f"/api/v1/match/matches/{mid}/publish", {"publish": True}, "md")
        self.assertEqual((st, body["publish_deeptens"]), (200, 1))
        st, body, _ = self.req("POST", f"/api/v1/match/matches/{mid}/publish", {"publish": "yes"}, "md")
        self.assertEqual(body["publish_deeptens"], 0, "only true publishes")
        cid = self.core.competitions.create("C", fid, "md")
        st, body, _ = self.req("POST", f"/api/v1/match/competitions/{cid}/publish", {"publish": True}, "md")
        self.assertEqual((st, body["publish_deeptens"]), (200, 1))
        self.assertEqual(self.req("POST", f"/api/v1/match/competitions/{cid}/publish", {"publish": True}, "ro")[0], 403)


if __name__ == "__main__":
    unittest.main()
