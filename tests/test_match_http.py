"""Match Control over HTTP: logins and roles, the cross-site guard, users managed in Range Control, the shooter's
Start record, and what a target's screen sees."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from fetserver.admin import Admin, write_password
from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server

BASE = 1_000_000_000
TOKEN = "dev-token"
ALICE = "alice-client-01"


class Clock:
    def __init__(self):
        self.t = BASE

    def __call__(self):
        return self.t


class MatchHttp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clock = Clock()
        self.core = Core(Database(str(self.dir / "t.db")), now_ms=self.clock, lease_ms=10 ** 9)
        self.server = make_server(self.core, TOKEN, "127.0.0.1", 0, admin=Admin(self.dir))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        write_password(self.dir, "range-pass")
        for did in ("lane-1", "lane-2"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})
        self.core.matches.set_user("md", "director", "director-pass")
        self.core.matches.set_user("ro", "officer", "officer-pass")
        self.cookies = {}

    def req(self, method, path, body=None, who=None, header=True):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        h = {"Content-Type": "application/json"}
        if header:
            h["X-FET-Admin"] = "1"
        if who and who in self.cookies:
            h["Cookie"] = self.cookies[who]
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"), r.getheader("Set-Cookie"))
        c.close()
        return out[0], out[1], out[2]

    def login(self, who, pw):
        st, body, cookie = self.req("POST", "/api/v1/match/login", {"username": who, "password": pw})
        if st == 200:
            self.cookies[who] = cookie.split(";")[0]
        return st, cookie

    def fmt(self, who="md"):
        st, f, _ = self.req("POST", "/api/v1/match/formats", {"format": {"name": "Quick", "phases": [
            {"kind": "sight", "label": "Sighting", "seconds": 60}, {"kind": "stage", "label": "Stage", "seconds": 120, "shots": 2}]}}, who)
        self.assertEqual(st, 200, f)
        return f["id"]

    def test_logins(self):
        st, body, _ = self.req("GET", "/api/v1/match/status")
        self.assertEqual((body["logged_in"], body["users_defined"]), (False, True))
        self.assertEqual(self.login("ro", "wrong-pass")[0], 401)
        self.assertEqual(self.login("nobody", "whatever-x")[0], 401)
        st, cookie = self.login("ro", "officer-pass")
        self.assertEqual(st, 200)
        for attr in ("fet_match=", "HttpOnly", "SameSite=Strict", "Path=/api/v1/match"):
            self.assertIn(attr, cookie)
        self.assertEqual(self.req("GET", "/api/v1/match/status", who="ro")[1]["role"], "officer")
        self.assertEqual(self.login("admin", "range-pass")[0], 200)
        self.assertEqual(self.req("GET", "/api/v1/match/status", who="admin")[1]["role"], "admin")
        self.assertEqual(self.req("GET", "/api/v1/match/matches", who="ro", header=False)[0], 403, "cross-site guard")
        self.assertEqual(self.req("GET", "/api/v1/match/matches")[0], 401)
        # a Match Control cookie is no Range Control login, and the other way round
        self.assertEqual(self.req("GET", "/api/v1/admin/targets", who="admin")[0], 401)

    def test_session_kinds_do_not_mix_and_cli_password_change_ends_sessions(self):
        self.login("ro", "officer-pass")
        st, body, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        rc_token = cookie.split(";")[0].split("=", 1)[1]
        mc_token = self.cookies["ro"].split("=", 1)[1]
        self.cookies["rc-as-match"] = "fet_match=" + rc_token
        self.cookies["match-as-rc"] = "fet_admin=" + mc_token
        self.assertEqual(self.req("GET", "/api/v1/match/matches", who="rc-as-match")[0], 401, "a Range Control token is no Match Control login")
        self.assertEqual(self.req("GET", "/api/v1/admin/targets", who="match-as-rc")[0], 401, "a Match Control token is no Range Control login")
        self.login("admin", "range-pass")                                   # admin's Match Control session carries the RC password
        self.cookies["admin-match-as-rc"] = "fet_admin=" + self.cookies["admin"].split("=", 1)[1]
        self.assertEqual(self.req("GET", "/api/v1/admin/targets", who="admin-match-as-rc")[0], 401, "still not a Range Control login")
        self.assertEqual(self.req("GET", "/api/v1/match/matches", who="ro")[0], 200)
        self.core.matches.set_user("ro", "officer", "changed-on-cli")          # like python3 -m fetserver match-user passwd
        self.assertEqual(self.req("GET", "/api/v1/match/matches", who="ro")[0], 401, "the old session ends with the old password")

    def test_roles(self):
        self.login("md", "director-pass")
        self.login("ro", "officer-pass")
        fid = self.fmt()
        self.assertEqual(self.req("POST", "/api/v1/match/formats", {"format": {"name": "x", "phases": []}}, "ro")[0], 403)
        lanes = [{"device_id": "lane-1", "competitor": "Alice"}, {"device_id": "lane-2"}]
        st, body, _ = self.req("POST", "/api/v1/match/matches", {"name": "Club", "format_id": fid, "lanes": lanes}, "ro")
        self.assertEqual(st, 403, "officers do not create matches")
        st, m, _ = self.req("POST", "/api/v1/match/matches", {"name": "Club", "format_id": fid, "lanes": lanes}, "md")
        self.assertEqual((st, m["state"]), (200, "ready"))
        mid = m["id"]
        self.assertEqual(self.req("POST", "/api/v1/match/matches", {"name": "Again", "format_id": fid, "lanes": lanes}, "md")[0], 400)
        st, m, _ = self.req("POST", f"/api/v1/match/matches/{mid}/start", {}, "ro")
        self.assertEqual((st, m["state"], m["clock"]["label"]), (200, "running", "Sighting"))
        st, m, _ = self.req("POST", f"/api/v1/match/matches/{mid}/command", {"action": "extra", "seconds": 30}, "ro")
        self.assertEqual(m["clock"]["remaining_ms"], 90_000)
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/command", {"action": "fly"}, "ro")[0], 400)
        # a shot during... nothing flagged yet; decisions need a director
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/flags/1", {"decision": "annul"}, "ro")[0], 403)
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/flags/1", {"decision": "annul"}, "md")[0], 400, "not flagged")
        st, lst, _ = self.req("GET", "/api/v1/match/matches", who="ro")
        self.assertEqual([x["name"] for x in lst["matches"]], ["Club"])
        st, tg, _ = self.req("GET", "/api/v1/match/targets", who="ro")
        self.assertEqual([t["in_match"] for t in tg["targets"]], [True, True])

    def test_users_managed_in_range_control_and_sessions_end(self):
        self.login("ro", "officer-pass")
        st, body, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["rc"] = cookie.split(";")[0]
        st, body, _ = self.req("POST", "/api/v1/admin/match-users", {"username": "ro", "role": "officer", "password": "brand-new-pass"}, "rc")
        self.assertEqual(st, 200)
        self.assertEqual(self.req("GET", "/api/v1/match/matches", who="ro")[0], 401, "new password ends the session")
        self.assertEqual(self.login("ro", "brand-new-pass")[0], 200)
        st, body, _ = self.req("POST", "/api/v1/admin/match-users", {"username": "x", "role": "officer", "password": "short"}, "rc")
        self.assertEqual(st, 400)
        st, body, _ = self.req("POST", "/api/v1/admin/match-users/ro/delete", {}, "rc")
        self.assertEqual((st, [u["username"] for u in body["users"]]), (200, ["md"]))
        self.assertEqual(self.req("GET", "/api/v1/match/matches", who="ro")[0], 401)
        self.assertEqual(self.req("GET", "/api/v1/admin/match-users")[0], 401, "Range Control login needed")

    def test_shooter_starts_record_and_sees_the_clock(self):
        self.login("md", "director-pass")
        fid = self.fmt()
        st, m, _ = self.req("POST", "/api/v1/match/matches", {"name": "N", "format_id": fid, "lanes": [{"device_id": "lane-1"}]}, "md")
        self.req("POST", f"/api/v1/match/matches/{m['id']}/start", {}, "md")
        self.core.acquire_control("lane-1", ALICE, "Alice")
        st, body, _ = self.req("POST", "/api/v1/targets/lane-1/start-record", {"client_id": ALICE}, header=False)
        self.assertEqual(st, 400, "not during sighting")
        self.clock.t = BASE + 61_000
        self.core.matches.tick()
        st, body, _ = self.req("POST", "/api/v1/targets/lane-1/start-record", {"client_id": "bob-client-0002"}, header=False)
        self.assertEqual(st, 403, "only whoever holds the lane")
        st, body, _ = self.req("POST", "/api/v1/targets/lane-1/start-record", {"client_id": ALICE}, header=False)
        self.assertEqual((st, body["match"]["in_record"]), (200, True))
        st, tg, _ = self.req("GET", f"/api/v1/targets?client_id={ALICE}", header=False)
        info = tg["targets"][0]["match"]
        self.assertEqual((info["name"], info["label"], info["kind"], info["in_record"]), ("N", "Stage", "stage", True))
        self.assertEqual(tg["targets"][0]["mode"], "record")
        self.assertEqual(self.req("POST", f"/api/v1/targets/lane-1/mode", {"client_id": ALICE, "mode": "practice"}, header=False)[0], 403, "locked")

    def test_restart_is_for_directors_and_the_board_is_public(self):
        self.login("md", "director-pass")
        self.login("ro", "officer-pass")
        fid = self.fmt()
        st, m, _ = self.req("POST", "/api/v1/match/matches", {"name": "B", "format_id": fid, "lanes": [{"device_id": "lane-1", "competitor": "Al"}],
                                                               "paced": "officer"}, "md")
        mid = m["id"]
        self.req("POST", f"/api/v1/match/matches/{mid}/start", {}, "ro")
        st, d, _ = self.req("POST", f"/api/v1/match/matches/{mid}/command", {"action": "go"}, "ro")
        self.assertEqual((st, d["state"]), (200, "running"))
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/restart", {"phase": 0}, "ro")[0], 403, "range officers ask the director")
        st, d, _ = self.req("POST", f"/api/v1/match/matches/{mid}/restart", {"phase": 0}, "md")
        self.assertEqual((st, d["state"], d["clock"]["phase"]), (200, "holding", 0))
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/restart", {"phase": 9}, "md")[0], 400)
        st, b, _ = self.req("GET", f"/api/v1/board?m={mid}", header=False)             # no login: like the target pages
        self.assertEqual((st, b["matches"][0]["name"], b["matches"][0]["rows"][0]["competitor"]), (200, "B", "Al"))
        st, b, _ = self.req("GET", "/api/v1/board", header=False)
        self.assertEqual([x["id"] for x in b["matches"]], [mid], "default: the current matches")
        self.assertEqual(self.req("GET", "/api/v1/board?m=x", header=False)[0], 400)
        self.assertEqual(self.req("GET", "/api/v1/board?m=999", header=False)[1]["matches"], [])

    def test_new_matches_default_to_spoken_commands(self):
        self.login("md", "director-pass")
        st, m, _ = self.req("POST", "/api/v1/match/matches", {"name": "D", "format_id": self.fmt(), "lanes": [{"device_id": "lane-1"}]}, "md")
        self.assertEqual((st, m["clock"]["paced"]), (200, "spoken"))

    def test_pacing_is_checked(self):
        self.login("md", "director-pass")
        st, body, _ = self.req("POST", "/api/v1/match/matches", {"name": "B", "format_id": self.fmt(), "lanes": [{"device_id": "lane-1"}], "paced": "maybe"}, "md")
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
