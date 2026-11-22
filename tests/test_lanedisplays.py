"""Lane displays (fetserver/lanedisplays.py): a CYD at a firing point, paired with one target by Range Control. Pairing
by a code the display shows; one display per target (a new one replaces it); the view it draws (scores written as the
screens write them, the public name only); rev and the long poll; its buttons through the lane's own rules."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from fetserver.lanedisplays import (MAX_PENDING, PAIR_LIFE_MS, DisplayError, NotAssigned, match_buttons, norm_code,
                                    score_text)
from fetcore import faces as F
from fetserver.athletes import SigninError
from tests.test_athletes import AL, JO, Case as LinkedCase
from tests.test_matches import BASE, MatchCase


class Rules(unittest.TestCase):
    def test_match_buttons_as_the_phone(self):
        run = {"state": "running"}
        self.assertEqual(match_buttons(dict(run, kind="stage", in_record=False)), ["start_record"])
        self.assertEqual(match_buttons(dict(run, kind="stage", in_record=True)), [], "record: no sighters in most books")
        self.assertEqual(match_buttons(dict(run, kind="stage", in_record=True, sighters_any_time=True)), ["back_to_sighters"])
        self.assertEqual(match_buttons(dict(run, kind="record", sighting=True)), ["start_record"], "sighting after a move")
        self.assertEqual(match_buttons(dict(run, kind="record", can_record=True, can_back=True)), ["start_record", "back_to_sighters"])
        self.assertEqual(match_buttons(dict(run, kind="record")), [])
        for st in ("paused", "ready", "holding", "overtime"):
            self.assertEqual(match_buttons({"state": st, "kind": "stage", "in_record": False}), [], st)
        self.assertEqual(match_buttons(None), [])

    def test_scores_as_the_screens_write_them(self):
        self.assertEqual(score_text({"miss": False, "ring": 10, "decimal": 10.4}, False), "10.4")
        self.assertEqual(score_text({"miss": False, "ring": 9, "decimal": 9.0}, False), "9.0")
        self.assertEqual(score_text({"miss": False, "ring": 10, "decimal": 10.4}, True), "10")
        self.assertEqual(score_text({"miss": True, "ring": 0, "decimal": 0.0}, False), "M")
        self.assertEqual(score_text(None, False), "")

    def test_codes_as_typed(self):
        self.assertEqual(norm_code("k7m-2qx"), "K7M2QX")
        self.assertEqual(norm_code(" K7M 2QX "), "K7M2QX")
        for bad in ("K7M2Q", "K7M2QXX", "K7M2Q0", "K7M2QI", None, 123456):
            with self.assertRaises(DisplayError):
                norm_code(bad)


class Base(MatchCase):
    def setUp(self):
        super().setUp()
        self.ld = self.core.lane_displays

    def paired(self, did="lane-1", hw="cyd-aaaaaa", name=None):
        p = self.ld.pair(hw, "0.1.0")
        self.ld.approve(p["code"], did, name)
        return p["token"]

    def view(self, token):
        return self.ld.view(self.ld.auth(token))

    def pairing(self, token):
        with self.assertRaises(NotAssigned) as cm:
            self.ld.auth(token)
        return cm.exception.pairing


class Pairing(Base):
    def test_pending_then_assigned(self):
        p = self.ld.pair("cyd-7e08f8", "0.1.0")
        self.assertRegex(p["code"], r"^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}$")
        self.assertTrue(p["token"].startswith("fetd_"))
        self.assertEqual(self.pairing(p["token"]), "pending", "shown the code, waiting for Range Control")
        d = self.ld.approve(p["code"].lower(), "lane-2", "Lane 2 screen")
        self.assertEqual((d["device_id"], d["name"], d["hw_id"], d["fw"]), ("lane-2", "Lane 2 screen", "cyd-7e08f8", "0.1.0"))
        self.assertEqual(self.view(p["token"])["target"]["device_id"], "lane-2")
        with self.assertRaisesRegex(DisplayError, "no display is showing that code"):
            self.ld.approve(p["code"], "lane-1")                    # a code works once
        self.assertEqual(self.pairing("fetd_nonsense"), "none")
        self.assertEqual(self.pairing(None), "none")
        row = self.core.db.conn().execute("SELECT token_hash FROM lane_displays").fetchone()[0]
        self.assertNotIn(p["token"], row, "only the token's SHA-256 is kept")

    def test_codes_expire_and_a_new_request_replaces_the_old(self):
        p = self.ld.pair("cyd-1")
        self.clock.t += PAIR_LIFE_MS
        self.assertEqual(self.pairing(p["token"]), "none", "expired: the display asks again")
        with self.assertRaisesRegex(DisplayError, "10 minutes"):
            self.ld.approve(p["code"], "lane-1")
        a = self.ld.pair("cyd-1")
        b = self.ld.pair("cyd-1")
        self.assertEqual(self.pairing(a["token"]), "none", "the same display asking again: its old code stops")
        self.assertEqual(self.pairing(b["token"]), "pending")

    def test_bounded_and_checked(self):
        for i in range(MAX_PENDING):
            self.ld.pair("cyd-%d" % i)
        with self.assertRaisesRegex(DisplayError, "too many displays waiting"):
            self.ld.pair("cyd-x")
        self.clock.t += PAIR_LIFE_MS
        self.ld.pair("cyd-x")                                       # expired ones make room
        for bad in ("", "a b", "x" * 41, None, 5):
            with self.assertRaisesRegex(DisplayError, "hw_id"):
                self.ld.pair(bad)
        with self.assertRaisesRegex(DisplayError, "fw"):
            self.ld.pair("cyd-y", "0.1 beta")
        p = self.ld.pair("cyd-z")
        for did in ("nope", None, 3):
            with self.assertRaisesRegex(DisplayError, "one of the range's targets"):
                self.ld.approve(p["code"], did)
        with self.assertRaisesRegex(DisplayError, "40 characters"):
            self.ld.approve(p["code"], "lane-1", "x" * 41)

    def test_one_display_per_target_and_one_target_per_display(self):
        a = self.paired("lane-1", "cyd-a")
        b = self.paired("lane-1", "cyd-b")
        self.assertEqual(self.pairing(a), "none", "a new display on the target replaces the old one")
        self.assertEqual(self.view(b)["target"]["device_id"], "lane-1")
        c = self.paired("lane-2", "cyd-b")
        self.assertEqual(self.pairing(b), "none", "paired again: its old pairing is gone")
        self.assertEqual([d["hw_id"] for d in self.ld.list()["displays"]], ["cyd-b"])
        self.assertEqual(self.view(c)["target"]["device_id"], "lane-2")

    def test_range_control_moves_renames_and_removes(self):
        a = self.paired("lane-1", "cyd-a")
        b = self.paired("lane-2", "cyd-b")
        ida = next(d["id"] for d in self.ld.list()["displays"] if d["hw_id"] == "cyd-a")
        self.ld.change(ida, "lane-2", "Front")
        self.assertEqual(self.pairing(b), "none", "moved onto lane-2: the display there is replaced")
        v = self.view(a)
        self.assertEqual((v["target"]["device_id"], v["display"]["name"]), ("lane-2", "Front"))
        self.ld.change(ida, name="  ")                              # a blank name changes nothing
        self.assertEqual(self.ld.get(ida)["name"], "Front")
        self.ld.remove(ida)
        self.assertEqual(self.pairing(a), "none")
        for f in (lambda: self.ld.remove(ida), lambda: self.ld.change(ida, name="x"), lambda: self.ld.get(ida)):
            with self.assertRaisesRegex(DisplayError, "no such display"):
                f()

    def test_list_for_range_control(self):
        self.paired("lane-1", "cyd-a", "Lane 1")
        self.ld.pair("cyd-waiting")
        out = self.ld.list()
        self.assertEqual(out["waiting"], 1, "how many displays are showing a code (not the codes)")
        d = out["displays"][0]
        self.assertEqual((d["name"], d["target_name"], d["lane"], d["fw"], d["online"]), ("Lane 1", "lane-1", 1, "0.1.0", True))
        self.clock.t += 120_000
        self.assertFalse(self.ld.list()["displays"][0]["online"], "not seen for 2 minutes")


class View(Base):
    def test_shots_scores_and_totals(self):
        tok = self.paired()
        v = self.view(tok)
        self.assertEqual((v["mode"], v["count"], v["shots"], v["last"], v["totals"]["score"]), ("practice", 0, [], None, "0.0"))
        self.assertEqual(v["face"]["id"], "issf-ar10")
        self.assertEqual(v["face"]["rings"][0], {"value": 10, "dia_mm": 0.5})
        self.shoot(x="1.00")                                       # (1.00, 0.50) mm and (-12.34, 0.50) mm
        self.shoot(x="-12.34")
        a, b = (F.score(F.face("issf-ar10"), x, 50) for x in (100, -1234))       # what fetcore says: the one scorer
        v = self.view(tok)
        self.assertEqual([(s["n"], s["x_h"], s["y_h"], s["score"]) for s in v["shots"]],
                         [(1, 100, 50, "%.1f" % (a.tenths / 10)), (2, -1234, 50, "%.1f" % (b.tenths / 10))])
        self.assertEqual(v["last"], {"n": 2, "score": "%.1f" % (b.tenths / 10), "inner": b.inner, "miss": False})
        self.assertEqual(v["totals"], {"shots": 2, "score": "%.1f" % ((a.tenths + b.tenths) / 10), "x": int(a.inner) + int(b.inner), "misses": 0})
        self.assertEqual(v["shots"][0]["inner"], a.inner)
        self.assertLess(len(json.dumps(v)), 4000, "small enough for the CYD")

    def test_whole_rings_on_a_conventional_face(self):
        tok = self.paired()
        self.core.set_face("lane-1", "nra-a17")                     # Range Control: the A-17 mask is on
        self.shoot(x="1.00")
        s = F.score(F.face("nra-a17"), 100, 50)
        v = self.view(tok)
        self.assertEqual((v["face"]["id"], v["face"]["decimal"]), ("nra-a17", False))
        self.assertEqual((v["shots"][0]["score"], v["totals"]["score"]), (str(s.ring), str(s.ring)), "whole rings, no decimals")

    def test_rev_changes_with_what_is_drawn_not_with_time(self):
        tok = self.paired()
        r1 = self.view(tok)["rev"]
        self.clock.t += 5000
        self.assertEqual(self.view(tok)["rev"], r1, "time alone changes nothing drawn")
        self.shoot()
        self.assertNotEqual(self.view(tok)["rev"], r1)

    def test_long_poll(self):
        tok = self.paired()
        rev = self.view(tok)["rev"]
        t0 = time.monotonic()
        self.assertEqual(self.ld.wait_view(tok, "other", 10)["rev"], rev, "a different rev: at once")
        self.assertEqual(self.ld.wait_view(tok, rev, 0.4)["rev"], rev, "nothing new: after the wait")
        self.assertLess(time.monotonic() - t0, 3)
        threading.Timer(0.3, self.shoot).start()
        t0 = time.monotonic()
        v = self.ld.wait_view(tok, rev, 20)
        self.assertNotEqual(v["rev"], rev)
        self.assertEqual(v["count"], 1)
        self.assertLess(time.monotonic() - t0, 3, "a shot reaches the display as soon as the server has it")
        self.assertEqual(self.core.hub.subscriber_count(), 0, "no subscription left behind")

    def test_removed_while_waiting(self):
        tok = self.paired()
        rev = self.view(tok)["rev"]
        did = self.ld.list()["displays"][0]["id"]
        threading.Timer(0.3, lambda: self.ld.remove(did)).start()
        t0 = time.monotonic()
        with self.assertRaises(NotAssigned):
            self.ld.wait_view(tok, rev, 20)
        self.assertLess(time.monotonic() - t0, 3, "told at once")

    def test_a_removed_target(self):
        tok = self.paired()
        with self.core.db.write() as c:
            c.execute("UPDATE targets SET removed=1 WHERE device_id='lane-1'")
        with self.assertRaises(NotAssigned) as cm:
            self.view(tok)
        self.assertEqual(cm.exception.pairing, "none", "Range Control assigns it again")


class Match(Base):
    def test_start_record_from_the_display(self):
        tok = self.paired()
        fid = self.custom([("stage", 300, 2)])
        mid = self.m.create("N", fid, [{"device_id": "lane-1"}], "dir")
        v = self.view(tok)
        self.assertEqual((v["match"]["state"], v["match"]["buttons"]), ("ready", []))
        with self.assertRaisesRegex(DisplayError, "not now"):
            self.ld.act(tok, {"action": "start_record"})
        self.m.start(mid, "ro")
        self.at(10)
        v = self.view(tok)
        self.assertEqual((v["match"]["name"], v["match"]["buttons"]), ("N", ["start_record"]))
        v = self.ld.act(tok, {"action": "start_record"})
        self.assertEqual((v["mode"], v["match"]["in_record"], v["match"]["buttons"]), ("record", True, []))
        with self.assertRaisesRegex(DisplayError, "not now"):
            self.ld.act(tok, {"action": "back_to_sighters"})        # ISSF-style book: no sighters after record
        r = self.core.db.conn().execute("SELECT device_id, data FROM match_log WHERE match_id=? AND kind='start_record'", (mid,)).fetchone()
        self.assertEqual((r["device_id"], json.loads(r["data"])["by"]), ("lane-1", "display Display aaaaaa"), "the log says it came from the display")

    def test_back_to_sighters_where_the_book_allows(self):
        tok = self.paired()
        fid = self.m.save_format({"name": "SB", "book": "NRA-SB", "phases": [
            {"kind": "stage", "label": "Prone", "seconds": 300, "shots": 2, "position": "prone"}]}, "dir")["id"]
        mid = self.m.create("SB", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(10)
        v = self.ld.act(tok, {"action": "start_record"})
        self.assertEqual(v["match"]["buttons"], ["back_to_sighters"], "NRA Smallbore 9.2")
        v = self.ld.act(tok, {"action": "back_to_sighters"})
        self.assertEqual((v["mode"], v["match"]["buttons"]), ("sighter", ["start_record"]))

    def test_not_in_a_match_and_unknown_actions(self):
        tok = self.paired()
        with self.assertRaisesRegex(DisplayError, "not in a match"):
            self.ld.act(tok, {"action": "start_record"})
        for bad in ({"action": "settings"}, {}, None, {"action": None}):
            with self.assertRaisesRegex(DisplayError, "action: one of"):
                self.ld.act(tok, bad)
        with self.assertRaisesRegex(DisplayError, "no claim"):
            self.ld.act(tok, {"action": "claim_cancel"})
        with self.assertRaisesRegex(DisplayError, "nobody is signed in"):
            self.ld.act(tok, {"action": "signout"})
        with self.assertRaisesRegex(SigninError, "not linked"):
            self.ld.act(tok, {"action": "signin", "number": "123456", "pin": "2580"})
        with self.assertRaises(NotAssigned):
            self.ld.act("fetd_unknown", {"action": "signout"})


class Linked(LinkedCase):
    """With the Pi linked to deepTENS: the QR code, PIN sign-in from the display, the public name only, claims."""

    def setUp(self):
        super().setUp()
        self.ld = self.core.lane_displays
        p = self.ld.pair("cyd-a")
        self.ld.approve(p["code"], "lane-1")
        self.tok = p["token"]

    def view(self):
        return self.ld.view(self.ld.auth(self.tok))

    def test_qr_then_pin_then_sign_out(self):
        v = self.view()
        self.assertIsNotNone(v["signin_url"], "nobody signed in: the lane's sign-in QR")
        self.assertTrue(v["pin_signin"])
        v = self.ld.act(self.tok, {"action": "signin", "number": "123456", "pin": "2580"})
        self.assertEqual(v["athlete"]["public_name"], "J. S.")
        self.assertNotIn("Jo Shot", json.dumps(v), "anyone at the lane sees only the public name")
        self.assertNotIn("123456", json.dumps(v), "nor the athlete number")
        self.assertIsNone(v["signin_url"])
        self.assertFalse(v["pin_signin"])
        self.assertTrue(v["can_sign_out"])
        v = self.ld.act(self.tok, {"action": "signout"})
        self.assertIsNone(v["athlete"])
        with self.assertRaisesRegex(SigninError, "wrong"):
            self.ld.act(self.tok, {"action": "signin", "number": "123456", "pin": "0000"})

    def test_no_sign_out_from_the_display_in_a_match(self):
        self.ld.act(self.tok, {"action": "signin", "number": "654321", "pin": "1357"})
        fid = self.custom([("stage", 300, 2)])
        mid = self.m.create("N", fid, [{"device_id": "lane-1"}], "dir", publish=False)
        self.m.start(mid, "ro")
        self.assertFalse(self.view()["can_sign_out"])
        with self.assertRaisesRegex(SigninError, "in a match"):
            self.ld.act(self.tok, {"action": "signout"})

    def test_a_poster_claim_and_not_you(self):
        ok, why = self.a.claim({"id": 7, "kind": "claim", "lane": 1, "athlete": AL})
        self.assertTrue(ok, why)
        v = self.view()
        self.assertEqual(v["claim"]["public_name"], "Al Aim")
        rev = v["rev"]
        self.clock.t += 5000
        self.assertEqual(self.view()["rev"], rev, "the countdown is the display's own")
        v = self.ld.act(self.tok, {"action": "claim_cancel"})
        self.assertIsNone(v["claim"])


class Http(unittest.TestCase):
    """The routes: the display's token in Authorization; pairing needs no login, approving needs Range Control."""

    def setUp(self):
        from fetserver.admin import Admin, write_password
        from fetserver.core import Core
        from fetserver.db import Database
        from fetserver.server import make_server
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = Core(Database(os.path.join(tmp.name, "t.db")))
        self.core.handle_heartbeat({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 0})
        write_password(tmp.name, "range-pass")
        self.srv = make_server(self.core, "tok", "127.0.0.1", 0, admin=Admin(tmp.name))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.cookie = None

    def req(self, path, body=None, headers=None, method=None):
        h = dict({"Content-Type": "application/json"}, **(headers or {}))
        if self.cookie:
            h["Cookie"] = self.cookie
        r = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=40) as resp:
                return resp.status, json.loads(resp.read() or b"null"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), e.headers

    def test_pair_approve_view_act(self):
        st, p, _ = self.req("/api/v1/display/pair", {"hw_id": "cyd-7e08f8", "fw": "0.1.0"})
        self.assertEqual(st, 200)
        bearer = {"Authorization": "Bearer " + p["token"]}
        self.assertEqual(self.req("/api/v1/display/view", headers=bearer)[:2], (401, {"error": "not assigned", "pairing": "pending"}))
        self.assertEqual(self.req("/api/v1/display/view")[:2], (401, {"error": "not assigned", "pairing": "none"}))
        approve = {"code": p["code"], "device_id": "lane-1", "name": "Lane 1"}
        self.assertEqual(self.req("/api/v1/admin/lane-displays", approve, {"X-FET-Admin": "1"})[0], 401, "approving needs Range Control")
        st, _, h = self.req("/api/v1/admin/login", {"password": "range-pass"}, {"X-FET-Admin": "1"})
        self.assertEqual(st, 200)
        self.cookie = h["Set-Cookie"].split(";")[0]
        st, body, _ = self.req("/api/v1/admin/lane-displays", approve, {"X-FET-Admin": "1"})
        self.assertEqual((st, body["displays"][0]["hw_id"]), (200, "cyd-7e08f8"))
        self.cookie = None
        st, v, _ = self.req("/api/v1/display/view", headers=dict(bearer, **{"X-FET-Display-FW": "0.1.1"}))
        self.assertEqual((st, v["target"]["device_id"]), (200, "lane-1"))
        self.assertEqual(self.req("/api/v1/display/view?wait=x", headers=bearer)[0], 400)
        threading.Timer(0.3, lambda: self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 1, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": 1,
             "raw": '{"shot":1, "miss":0, "x":1.00, "y":0.50, "N":1, "E":2, "S":3, "W":0}'}]})).start()
        t0 = time.monotonic()
        st, v2, _ = self.req("/api/v1/display/view?rev=%s&wait=20" % v["rev"], headers=bearer)
        self.assertEqual((st, v2["count"]), (200, 1))
        self.assertLess(time.monotonic() - t0, 3, "the long poll answers when the shot arrives")
        self.assertEqual(self.req("/api/v1/display/action", {"action": "start_record"}, bearer)[:2], (400, {"error": "this target is not in a match"}))
        self.assertEqual(self.req("/api/v1/display/action", {"action": "x"})[0], 401)
        self.assertEqual(self.req("/api/v1/display/pair", {"hw_id": "bad id"})[0], 400)
        st, _, h = self.req("/api/v1/admin/login", {"password": "range-pass"}, {"X-FET-Admin": "1"})
        self.cookie = h["Set-Cookie"].split(";")[0]
        st, lst, _ = self.req("/api/v1/admin/lane-displays", headers={"X-FET-Admin": "1"})
        self.assertEqual((st, lst["displays"][0]["fw"]), (200, "0.1.1"), "the version the display sends")
        did = lst["displays"][0]["id"]
        self.assertEqual(self.req("/api/v1/admin/lane-displays/%d" % did, {"name": "Front"}, {"X-FET-Admin": "1"})[1]["displays"][0]["name"], "Front")
        self.assertEqual(self.req("/api/v1/admin/lane-displays/%d/delete" % did, {}, {"X-FET-Admin": "1"})[0], 200)
        self.assertEqual(self.req("/api/v1/admin/lane-displays/%d/delete" % did, {}, {"X-FET-Admin": "1"})[0], 404)
        self.cookie = None
        self.assertEqual(self.req("/api/v1/display/view", headers=bearer)[:2], (401, {"error": "not assigned", "pairing": "none"}))


if __name__ == "__main__":
    unittest.main()


class Sights(Base):
    """Sights on the lane's display (user 2026-11-22: "The range control needs an option to switch a CYD into 'sights'
    mode. The shooter should be able to estimate their adjustment, it would be helpful to show the calculated
    adjustments on the screen at the command of the range master"; "clicks per ring should default to 4, be settable
    when the Sights command gets sent ... shooter sees clicks. Nothing should ever talk about rings")."""
    def setUp(self):
        super().setUp()
        self.tok = self.paired()
        self.core.set_mode("lane-1", None, "practice", admin=True)
        self.shoot("lane-1", "3.00")                             # before sights: not in the group

    def sights(self):
        return self.view(self.tok)["sights"]

    def test_start_estimate_then_show_clicks(self):
        self.assertIsNone(self.sights(), "off until the range master starts it")
        st = self.core.set_sights("lane-1", "start")
        self.assertEqual((st["show"], st["cpr"], st["shots"]), (False, 4.0, 0), "clicks per ring: 4 unless set")
        for x in ("1.00", "1.20", "0.80"):
            self.shoot("lane-1", x)
        g = self.sights()
        self.assertEqual(sorted(p["n"] for p in g["shots"]), [2, 3, 4], "the shots since sights started")
        self.assertTrue(g["ready"])
        self.assertEqual(g["center_h"], [100, 50])
        self.assertIsNone(g["clicks"], "the shooter estimates first")
        self.core.set_sights("lane-1", "show")
        g = self.sights()
        self.assertEqual(g["clicks"], {"x": -2, "y": -1}, "1.0 mm right, 0.5 mm high: 2 clicks left, 1 click down at 4 per ring")
        self.assertNotIn("ring", json.dumps(g).replace("ring_mm", ""), "never rings for the shooter")
        self.core.set_sights("lane-1", "hide")
        self.assertIsNone(self.sights()["clicks"])

    def test_clicks_per_ring_set_with_the_command_and_kept_for_the_lane(self):
        self.core.set_sights("lane-1", "start", 8)
        for x in ("1.00", "1.20", "0.80"):
            self.shoot("lane-1", x)
        self.core.set_sights("lane-1", "show")
        self.assertEqual(self.sights()["clicks"], {"x": -3, "y": -2}, "8 per ring: 3.2 and 1.6")
        self.core.set_sights("lane-1", "end")
        self.assertIsNone(self.sights())
        self.assertEqual(self.core.set_sights("lane-1", "start")["cpr"], 8.0, "the lane's last value")
        for bad in (0, -1, 101, "4", True):
            with self.assertRaises(Exception):
                self.core.set_sights("lane-1", "start", bad)

    def test_a_new_group_after_an_adjustment(self):
        self.core.set_sights("lane-1", "start")
        for x in ("1.00", "1.20", "0.80"):
            self.shoot("lane-1", x)
        self.core.set_sights("lane-1", "show")
        self.core.set_sights("lane-1", "group")
        g = self.sights()
        self.assertEqual((g["shots"], g["ready"], g["show"], g["clicks"]), ([], False, False, None), "from the next shot, hidden")
        self.shoot("lane-1", "0.00")
        self.assertEqual([p["n"] for p in self.sights()["shots"]], [5], "shown before there are enough for a suggestion")

    def test_practice_only(self):
        with self.assertRaisesRegex(Exception, "start sights first"):
            self.core.set_sights("lane-1", "show")
        with self.assertRaisesRegex(Exception, "action must be"):
            self.core.set_sights("lane-1", "rings")
        self.core.set_sights("lane-1", "start")
        self.core.set_mode("lane-1", None, "sighter", admin=True)
        self.assertIsNone(self.sights(), "the lane left practice: the normal screen")
        with self.assertRaisesRegex(Exception, "practice only"):
            self.core.set_sights("lane-1", "start")
        self.core.set_mode("lane-1", None, "practice", admin=True)
        self.assertIsNotNone(self.sights(), "back in practice: on again (a new series: from its first shot)")
        self.core.set_sights("lane-1", "end")

    def test_range_control_sees_it(self):
        self.core.set_sights("lane-1", "start", 5)
        t = [x for x in self.core.admin_view()["targets"] if x["device_id"] == "lane-1"][0]
        self.assertEqual((t["sights"]["show"], t["sights_cpr"]), (False, 5.0))

    def test_clicks_round_as_the_lane_view(self):
        c = self.core.clicks
        self.assertEqual([c(0.3125, 4, 2.5), c(-0.3125, 4, 2.5), c(0.3, 4, 2.5), c(0.0, 4, 2.5), c(-0.1, 4, 2.5)], [1, -1, 0, 0, 0],
                         "half a click rounds away from zero; nothing is 0, never -0")
