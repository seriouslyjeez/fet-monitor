import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server
from fetserver.simtarget import SimTarget

TOKEN = "test-token"


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "t.db"))
        self.core = Core(self.db)
        self.server = make_server(self.core, TOKEN, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None, token=TOKEN, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if token is not None:
            req.add_header("X-FET-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class Api(ServerCase):
    def test_health_needs_no_token(self):
        status, body = self.call("GET", "/api/v1/health", token=None)
        self.assertEqual((status, body["ok"]), (200, True))

    def test_device_endpoints_require_the_token(self):
        payload = {"device_id": "a1", "boot_count": 1, "uptime_ms": 1, "events": []}
        for tok in (None, "wrong", ""):
            self.assertEqual(self.call("POST", "/api/v1/events", payload, token=tok)[0], 401)
            self.assertEqual(self.call("POST", "/api/v1/heartbeat", payload, token=tok)[0], 401)
        self.assertEqual(self.call("POST", "/api/v1/events", payload)[0], 200)
        self.assertEqual([t["device_id"] for t in self.core.list_targets()], ["a1"])

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/v1/events", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/v1/events", {"device_id": "a1"})[0], 400)
        self.assertEqual(self.call("GET", "/api/v1/nope")[0], 404)
        self.assertEqual(self.call("POST", "/api/v1/nope", {})[0], 404)
        status, _ = self.call("POST", "/api/v1/events", raw=b"x" * (300 * 1024))
        self.assertEqual(status, 413)

    def test_end_to_end_shot_and_commands(self):
        sim = SimTarget(self.url, TOKEN, "lane-1", "Lane 1")
        sim.heartbeat()
        sim.fire(-3.61, -14.56)
        sim.fire_miss()
        self.assertTrue(sim.deliver())
        status, body = self.call("GET", "/api/v1/targets/lane-1/shots", token=None)
        self.assertEqual(status, 200)
        shots = body["shots"]
        self.assertEqual([(s["ring"], s["decimal"], s["miss"]) for s in shots], [(4, 4.9, False), (0, 0.0, True)])
        status, body = self.call("GET", "/api/v1/targets", token=None)
        self.assertEqual((body["targets"][0]["name"], body["targets"][0]["online"], body["targets"][0]["shot_count"]),
                         ("Lane 1", True, 2))
        self.assertEqual(self.call("GET", f"/api/v1/targets/lane-1/shots?since_id={shots[0]['id']}", token=None)[1]["shots"][0]["miss"], True)
        status, body = self.call("POST", "/api/v1/targets/lane-1/commands", {"type": "serial", "line": '{"NEW":1}'}, token=None)
        self.assertEqual(status, 200)
        sim.heartbeat()
        self.assertEqual([c["line"] for c in sim.executed], ['{"NEW":1}'])
        sim.heartbeat()                                      # result acknowledged, nothing more to run
        self.assertEqual(len(sim.executed), 1)
        self.assertEqual(self.call("POST", "/api/v1/targets/lane-1/commands", {"type": "serial", "line": '{"INIT":0}'}, token=None)[0], 400)
        self.assertEqual(self.call("POST", "/api/v1/targets/ghost/commands", {"type": "reboot"}, token=None)[0], 404)


class Resilience(ServerCase):
    def test_outage_lost_acks_and_reboots_deliver_every_shot_exactly_once(self):
        sim = SimTarget(self.url, TOKEN, "lane-2", seed=3, ack_loss=0.4, batch_size=4)
        good_url = sim.url
        sim.send_config()
        for i in range(12):
            sim.fire()
        sim.url = "http://127.0.0.1:1"                       # server unreachable
        for i in range(10):
            sim.fire()
        self.assertFalse(sim.deliver())
        sim.reboot()                                         # power cycle with a full queue (flash survives)
        for i in range(8):
            sim.fire()
        sim.reboot()
        for i in range(5):
            sim.fire_miss()
        sim.url = good_url
        for _ in range(200):                                 # lost acknowledgements force repeated sends
            if sim.deliver():
                break
        self.assertEqual(sim.queue, [])
        c = self.db.conn()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM shots").fetchone()[0], 12 + 10 + 8 + 5)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1 + 12 + 10 + 8 + 5)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM (SELECT DISTINCT boot_count, seq FROM events)").fetchone()[0],
                         1 + 12 + 10 + 8 + 5)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM shots WHERE is_miss=1").fetchone()[0], 5)
        self.assertEqual(c.execute("SELECT COUNT(DISTINCT boot_count) FROM events").fetchone()[0], 3)

    def test_server_restart_loses_nothing(self):
        sim = SimTarget(self.url, TOKEN, "lane-3", batch_size=5)
        for _ in range(10):
            sim.fire()
        self.assertTrue(sim.deliver())
        self.stop()                                          # hard stop, then a new server on the same file
        db2 = Database(os.path.join(self.tmp.name, "t.db"))
        server2 = make_server(Core(db2), TOKEN, "127.0.0.1", 0)
        threading.Thread(target=server2.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server2.shutdown(), server2.server_close()))
        sim.url = f"http://127.0.0.1:{server2.server_address[1]}"
        for _ in range(4):
            sim.fire()
        self.assertTrue(sim.deliver())
        self.assertEqual(db2.conn().execute("SELECT COUNT(*) FROM shots").fetchone()[0], 14)

    def test_concurrent_targets(self):
        sims = [SimTarget(self.url, TOKEN, f"t{i:03d}", seed=i, batch_size=7) for i in range(20)]
        for s in sims:
            for _ in range(15):
                s.fire()
        errors = []

        def run(s):
            try:
                s.heartbeat()
                if not s.deliver():
                    errors.append(s.device_id)
            except Exception as exc:   # pragma: no cover
                errors.append(repr(exc))

        threads = [threading.Thread(target=run, args=(s,)) for s in sims]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(self.db.conn().execute("SELECT COUNT(*) FROM shots").fetchone()[0], 20 * 15)
        self.assertEqual(len(self.core.list_targets()), 20)


if __name__ == "__main__":
    unittest.main()
