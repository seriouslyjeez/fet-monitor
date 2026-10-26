"""Range Control over HTTP: login, cookie, the cross-site guard, and the actions."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from fetserver.admin import Admin, write_password
from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server

TOKEN = "dev-token"
ALICE = "alice-client-01"
SHOT = '{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":1.00, "y":1.00 , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'


class AdminHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.core = Core(Database(str(self.dir / "t.db")))
        self.server = make_server(self.core, TOKEN, "127.0.0.1", 0, admin=Admin(self.dir))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.cookie = None

    def req(self, method, path, body=None, admin_header=True, cookie=True, origin=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        if admin_header:
            h["X-FET-Admin"] = "1"
        if cookie and self.cookie:
            h["Cookie"] = self.cookie
        if origin:
            h["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"), r.getheader("Set-Cookie"))
        c.close()
        return out

    def login(self, pw="range-pass"):
        st, body, set_cookie = self.req("POST", "/api/v1/admin/login", {"password": pw})
        if st == 200:
            self.cookie = set_cookie.split(";")[0]
        return st, body, set_cookie

    def heartbeat(self, did):
        st, _, _ = self.req("POST", "/api/v1/heartbeat", {"device_id": did, "boot_count": 1, "uptime_ms": 1000, "name": did},
                            admin_header=False, headers={"X-FET-Token": TOKEN})
        return st

    def test_no_password_yet(self):
        st, body, _ = self.req("GET", "/api/v1/admin/status")
        self.assertEqual((st, body), (200, {"password_set": False, "logged_in": False}))
        self.assertEqual(self.login()[0], 409)
        self.assertEqual(self.req("GET", "/api/v1/admin/targets")[0], 401)

    def test_login_cookie_and_logout(self):
        write_password(self.dir, "range-pass")
        st, body, set_cookie = self.login("nope-nope")
        self.assertEqual((st, set_cookie), (401, None))
        st, body, set_cookie = self.login()
        self.assertEqual(st, 200)
        for attr in ("HttpOnly", "SameSite=Strict", "Path=/api/v1/admin", "Max-Age="):
            self.assertIn(attr, set_cookie)
        self.assertEqual(self.req("GET", "/api/v1/admin/status")[1]["logged_in"], True)
        self.assertEqual(self.req("GET", "/api/v1/admin/targets")[0], 200)
        st, _, cleared = self.req("POST", "/api/v1/admin/logout", {})
        self.assertIn("Max-Age=0", cleared)
        self.assertEqual(self.req("GET", "/api/v1/admin/targets")[0], 401, "the old cookie is dead")

    def test_cross_site_requests_are_refused(self):
        write_password(self.dir, "range-pass")
        self.login()
        self.assertEqual(self.req("GET", "/api/v1/admin/targets", admin_header=False)[0], 403, "no custom header")
        self.assertEqual(self.req("POST", "/api/v1/admin/login", {"password": "range-pass"}, admin_header=False)[0], 403)
        self.assertEqual(self.req("GET", "/api/v1/admin/targets", origin="http://evil.example")[0], 403, "foreign Origin")
        self.assertEqual(self.req("GET", "/api/v1/admin/targets", origin=f"http://127.0.0.1:{self.port}")[0], 200, "own Origin")

    def test_lockout_over_http(self):
        write_password(self.dir, "range-pass")
        for _ in range(5):
            self.login("nope-nope")
        st, body, _ = self.login()
        self.assertEqual(st, 429)
        self.assertGreater(body["retry_after"], 0)

    def test_actions(self):
        write_password(self.dir, "range-pass")
        for did in ("lane-1", "lane-2"):
            self.assertEqual(self.heartbeat(did), 200)
        self.core.acquire_control("lane-1", ALICE, "Alice")
        # not logged in: refused
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/lane-1/mode", {"mode": "sighter"})[0], 401)
        self.login()
        st, body, _ = self.req("POST", "/api/v1/admin/targets/lane-1/mode", {"mode": "sighter"})
        self.assertEqual((st, body["mode"], body["shooter"]), (200, "sighter", "Alice – Match"))
        st, body, _ = self.req("POST", "/api/v1/admin/targets/lane-2/mode", {"mode": "ammo"})
        self.assertEqual((st, body["kind"]), (200, "ammo"))
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/lane-2/mode", {"mode": "bogus"})[0], 400)
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/nope/mode", {"mode": "practice"})[0], 404)
        # remove: gone until it reports again
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/lane-2/remove", {})[0], 200)
        self.assertEqual([t["device_id"] for t in self.req("GET", "/api/v1/admin/targets")[1]["targets"]], ["lane-1"])
        self.heartbeat("lane-2")
        self.assertEqual(len(self.req("GET", "/api/v1/admin/targets")[1]["targets"]), 2)
        # ban: its reports get 403 and nothing is stored
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/lane-2/ban", {})[0], 200)
        self.assertEqual(self.heartbeat("lane-2"), 403)
        st, _, _ = self.req("POST", "/api/v1/events", {"device_id": "lane-2", "boot_count": 1, "uptime_ms": 2000, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": 1500, "raw": SHOT % 1}]}, admin_header=False, headers={"X-FET-Token": TOKEN})
        self.assertEqual(st, 403)
        view = self.req("GET", "/api/v1/admin/targets")[1]
        self.assertEqual(([t["device_id"] for t in view["targets"]], [b["device_id"] for b in view["banned"]]), (["lane-1"], ["lane-2"]))
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/lane-2/unban", {})[0], 200)
        self.assertEqual(self.heartbeat("lane-2"), 200)
        self.assertEqual(self.req("GET", "/api/v1/admin/targets")[1]["banned"], [])
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/lane-2/explode", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
