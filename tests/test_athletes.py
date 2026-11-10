"""Target sign-in on the Pi (P2, fetserver/athletes.py) against a fake deepTENS: PINs online and offline, phones through
the link, one athlete per lane and one lane per athlete, a fresh series in the athlete's name, idle and 4-hour
sign-outs (never in a match), sessions sent with exactly the shots fired in them (late ones too), match results tagged
with the athlete signed in on the firing point, and everything ended by unlinking."""
import json
import tempfile

from fetserver.admin import hash_password
from fetserver.athletes import CODE_LIFE_MS, SigninError
from fetserver.deeptens import DeepTens
from tests.test_deeptens_link import FakeDeepTens
from tests.test_matches import MatchCase

JO = {"athlete_id": 41, "number": "123456", "name": "Jo Shot", "public_name": "J. S."}
AL = {"athlete_id": 42, "number": "654321", "name": "Al Aim", "public_name": "Al Aim"}


class Fake(FakeDeepTens):
    def __init__(self):
        super().__init__()
        self.pins = {("123456", "2580"): JO, ("654321", "1357"): AL}
        self.athletes = []                 # roster: the range's athletes
        self.pending = []                  # phone sign-ins waiting for the Pi
        self.acks = []
        self.idle = 15
        self.pin_calls = 0

    def handle(self, path, body, auth):
        if not self.down and auth == "Bearer " + self.token and self.linked:
            if path == "/api/monitor/v1/athletes/pin":
                self.pin_calls += 1
                a = self.pins.get((body.get("number"), body.get("pin")))
                return (200, {"athlete": a}) if a else (403, {"error": "wrong athlete number or PIN"})
            if path == "/api/monitor/v1/signins":
                self.acks += body.get("acks", [])
                out, self.pending = self.pending, []
                return 200, {"signins": out}
            if path == "/api/monitor/v1/roster":
                return 200, {"range": {"id": 3, "name": "Riverside"}, "members": self.members, "athletes": self.athletes,
                             "signin_idle_min": self.idle}
        return super().handle(path, body, auth)


class Case(MatchCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fake = Fake()
        self.addCleanup(self.fake.close)
        self.dt = DeepTens(self.core, tmp.name, clock=lambda: self.clock.t / 1000, name=lambda: "Club Pi")
        self.core.deeptens = self.dt
        self.dt.start_link(self.fake.url)
        self.fake.approved = True
        self.dt.next_poll = 0
        self.dt.step()
        self.a = self.core.athletes

    def step(self, seconds=0):
        self.clock.t += int(seconds * 1000)
        self.dt.next_hb = self.dt.next_tick = self.dt.next_signins = self.dt.next_send = 0
        self.dt.step()

    def target(self, did="lane-1"):                       # the staff view: the athlete's id and full name too
        return next(t for t in self.core.list_targets(staff=True) if t["device_id"] == did)

    def sent(self, key):
        return self.fake.stored.get(key, (0, None))[1]


class Pin(Case):
    def test_online_a_fresh_series_in_their_name(self):
        self.shoot("lane-1")                                  # before anyone signed in: not theirs
        old = self.target()["series_id"]
        s = self.a.pin_sign_in("lane-1", "123 456", "2580")
        self.assertEqual((s["athlete_id"], s["via"], s["name"]), (41, "pin", "Jo Shot"))
        t = self.target()
        self.assertEqual(t["athlete"]["public_name"], "J. S.")
        self.assertNotEqual(t["series_id"], old)
        self.assertEqual(t["shooter"], "J. S.", "the series in their results name")
        self.assertIsNone(t["signin_url"], "no QR while someone is signed in")
        self.assertEqual(self.a.pin_sign_in("lane-1", "123456", "2580")["id"], s["id"], "again: the same session")

    def test_only_staff_see_the_full_name_and_number(self):
        self.a.pin_sign_in("lane-1", "123456", "2580")
        pub = next(t for t in self.core.list_targets() if t["device_id"] == "lane-1")["athlete"]
        self.assertEqual(set(pub), {"public_name", "via", "started_ms"}, "anyone on the network: the public name only")
        self.assertEqual(pub["public_name"], "J. S.")
        staff = next(t for t in self.core.list_targets(staff=True) if t["device_id"] == "lane-1")["athlete"]
        self.assertEqual((staff["name"], staff["number"], staff["athlete_id"]), ("Jo Shot", "123456", 41))
        rc = next(t for t in self.core.admin_view()["targets"] if t["device_id"] == "lane-1")["athlete"]
        self.assertEqual(rc["name"], "Jo Shot", "Range Control is staff")
        self.assertNotIn("Jo Shot", json.dumps(self.core.list_targets()), "nowhere in the public list")
        self.assertNotIn("123456", json.dumps(self.core.list_targets()))

    def test_wrong_pins_lock_the_number_here_too(self):
        for _ in range(5):
            with self.assertRaisesRegex(SigninError, "wrong"):
                self.a.pin_sign_in("lane-1", "123456", "0000")
        calls = self.fake.pin_calls
        with self.assertRaisesRegex(SigninError, "15 minutes"):
            self.a.pin_sign_in("lane-1", "123456", "2580")
        self.assertEqual(self.fake.pin_calls, calls, "not even asked")
        self.clock.t += 15 * 60 * 1000
        self.assertEqual(self.a.pin_sign_in("lane-1", "123456", "2580")["athlete_id"], 41)

    def test_bad_input(self):
        for n, p in (("12345", "2580"), ("123456", "25"), ("123456", "abcd"), (None, None)):
            with self.assertRaises(SigninError):
                self.a.pin_sign_in("lane-1", n, p)

    def test_offline_only_the_ranges_athletes(self):
        self.fake.athletes = [dict(JO, pin=hash_password("2580", alg="pbkdf2-sha256"))]
        self.dt.next_roster = 0
        self.step()
        self.fake.down = 503
        with self.assertRaisesRegex(SigninError, "wrong"):
            self.a.pin_sign_in("lane-1", "123456", "9999")
        with self.assertRaisesRegex(SigninError, "only athletes on this range"):
            self.a.pin_sign_in("lane-2", "654321", "1357")
        s = self.a.pin_sign_in("lane-1", "123456", "2580")
        self.assertEqual((s["athlete_id"], s["via"]), (41, "pin_offline"))

    def test_offline_without_a_roster(self):
        self.fake.down = 503
        with self.assertRaisesRegex(SigninError, "no recent list"):
            self.a.pin_sign_in("lane-1", "123456", "2580")

    def test_not_linked(self):
        self.dt.unlink()
        with self.assertRaisesRegex(SigninError, "not linked"):
            self.a.pin_sign_in("lane-1", "123456", "2580")


class OneEach(Case):
    def test_a_lane_in_use_and_moving_lanes(self):
        self.a.pin_sign_in("lane-1", "123456", "2580")
        with self.assertRaisesRegex(SigninError, "in use"):
            self.a.pin_sign_in("lane-1", "654321", "1357")
        self.a.pin_sign_in("lane-2", "123456", "2580")
        self.assertIsNone(self.target("lane-1")["athlete"], "signing in at lane 2 ended lane 1")
        self.assertEqual(self.target("lane-2")["athlete"]["athlete_id"], 41)
        r = self.core.db.conn().execute("SELECT end_why FROM athlete_sessions WHERE device_id='lane-1'").fetchone()
        self.assertEqual(r["end_why"], "elsewhere")

    def test_sign_out_starts_a_series_in_no_ones_name(self):
        self.a.pin_sign_in("lane-1", "123456", "2580")
        self.a.sign_out("lane-1")
        t = self.target()
        self.assertIsNone(t["athlete"])
        self.assertNotIn("J. S.", t["shooter"] or "")
        self.assertTrue(t["signin_url"])
        self.assertIsNone(self.a.sign_out("lane-1"), "nobody there")

    def test_no_lane_no_sign_in(self):
        self.core.set_lane("lane-2", None)
        with self.assertRaisesRegex(SigninError, "no lane"):
            self.a.pin_sign_in("lane-2", "123456", "2580")


class Phone(Case):
    def qr(self, did="lane-1"):
        url = self.target(did)["signin_url"]
        self.assertTrue(url.startswith(self.fake.url + "/t/7/"), url)
        return url.rstrip("/").split("/")[-1]

    def test_the_qr_code_and_the_round_through_deeptens(self):
        code = self.qr()
        lane = self.target()["lane"]
        self.fake.pending = [{"id": 9, "kind": "in", "lane": lane, "code": code, "athlete": JO}]
        self.step()
        self.assertEqual(self.target()["athlete"]["via"], "phone")
        self.step()
        self.assertEqual(self.fake.acks, [{"id": 9, "ok": True, "why": ""}])
        self.fake.pending = [{"id": 10, "kind": "in", "lane": self.target("lane-2")["lane"], "code": code, "athlete": AL}]
        self.step(); self.step()
        self.assertEqual(self.fake.acks[-1]["ok"], False, "another lane's code")
        self.fake.pending = [{"id": 11, "kind": "out", "lane": lane, "code": "x", "athlete": AL}]
        self.step(); self.step()
        self.assertEqual(self.fake.acks[-1], {"id": 11, "ok": False, "why": "someone else is signed in on this lane"})
        self.fake.pending = [{"id": 12, "kind": "out", "lane": lane, "code": "x", "athlete": JO}]
        self.step(); self.step()
        self.assertTrue(self.fake.acks[-1]["ok"])
        self.assertIsNone(self.target()["athlete"])

    def test_codes_change_at_each_sign_in_and_every_half_hour(self):
        c1 = self.qr()
        self.a.pin_sign_in("lane-1", "123456", "2580")
        self.a.sign_out("lane-1")
        c2 = self.qr()
        self.assertNotEqual(c1, c2)
        self.assertFalse(self.a.code_ok("lane-1", c1, self.clock.t), "a photo of the old one is useless")
        self.clock.t += CODE_LIFE_MS
        c3 = self.qr()
        self.assertNotEqual(c2, c3)
        self.assertTrue(self.a.code_ok("lane-1", c2, self.clock.t), "the previous one still works for a while")
        self.clock.t += 2 * CODE_LIFE_MS
        self.assertFalse(self.a.code_ok("lane-1", c3, self.clock.t))


class Timeouts(Case):
    def test_idle_after_the_ranges_time_without_a_shot(self):
        self.fake.idle = 20
        self.dt.next_roster = 0
        self.step()
        self.a.pin_sign_in("lane-1", "123456", "2580")
        self.step(19 * 60)
        self.shoot("lane-1")
        self.step(19 * 60)
        self.assertIsNotNone(self.target()["athlete"], "a shot keeps them signed in")
        self.step(61)
        self.assertIsNone(self.target()["athlete"])
        why = self.core.db.conn().execute("SELECT end_why FROM athlete_sessions").fetchone()[0]
        self.assertEqual(why, "idle")

    def test_four_hours_at_most(self):
        self.a.pin_sign_in("lane-1", "123456", "2580")
        for _ in range(4 * 6):
            self.step(10 * 60 - 1)
            self.shoot("lane-1")
        self.step(30)
        self.assertIsNone(self.target()["athlete"])
        self.assertEqual(self.core.db.conn().execute("SELECT end_why FROM athlete_sessions").fetchone()[0], "limit")

    def test_never_in_a_match_and_the_competitor_is_the_athlete(self):
        fid = self.custom([("sight", 30, 0), ("record", 60, 3)])
        self.a.pin_sign_in("lane-1", "123456", "2580")
        mid = self.m.create("League", fid, [{"device_id": "lane-1"}, {"device_id": "lane-2", "competitor": "Typed Name"}], "md", publish=True)
        self.a.pin_sign_in("lane-2", "654321", "1357")
        lanes = {r["device_id"]: r["competitor"] for r in self.core.db.conn().execute("SELECT * FROM match_lanes WHERE match_id=?", (mid,))}
        self.assertEqual(lanes, {"lane-1": "J. S.", "lane-2": "Typed Name"}, "the athlete's results name; a name the Match Director typed stays")
        with self.assertRaisesRegex(SigninError, "in a match"):
            self.a.sign_out("lane-1")
        self.step(5 * 3600)
        self.assertIsNotNone(self.target()["athlete"], "no idle time and no 4-hour limit in a match")
        self.assertEqual(self.a.sign_out("lane-1", "staff", staff=True)["end_why"], "staff", "staff can")

    def test_signed_in_after_the_match_was_made(self):
        fid = self.custom([("sight", 30, 0), ("record", 60, 3)])
        mid = self.m.create("League", fid, [{"device_id": "lane-1"}], "md", publish=True)
        self.a.pin_sign_in("lane-1", "123456", "2580")
        self.assertEqual(self.core.db.conn().execute("SELECT competitor FROM match_lanes WHERE match_id=?", (mid,)).fetchone()[0], "J. S.")


class Sending(Case):
    def test_the_session_with_exactly_its_shots_late_ones_too(self):
        self.shoot("lane-1", "5.00")                          # before: not theirs
        self.clock.t += 1000
        s = self.a.pin_sign_in("lane-1", "123456", "2580")
        self.step(1)
        self.shoot("lane-1", "1.00")
        self.shoot("lane-1", "2.00")
        self.step(1)
        d = self.sent(f"s{s['id']}")
        self.assertEqual((d["athlete_id"], d["via"], d["ended_ms"]), (41, "pin", None))
        self.assertEqual(sum(len(x["scored"]["shots"]) for x in d["series"]), 2)
        self.assertEqual(d["series"][0]["name"], "J. S.")
        self.assertTrue(d["series"][0]["face"]["rings"])
        self.clock.t += 1000
        self.a.sign_out("lane-1")
        # a shot fired just before the sign-out that arrives after it: still theirs (event time)
        n = self.seq["lane-1"] = self.seq["lane-1"] + 1
        from tests.test_matches import BASE, SHOT
        self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": self.clock.t - BASE, "events": [
            {"seq": n, "type": "shot", "uptime_ms": self.clock.t - BASE - 1500, "raw": SHOT % (n, "3.00")}]})
        self.step(1)
        self.shoot("lane-1", "4.00")                          # after: not theirs
        self.step(1)
        d = self.sent(f"s{s['id']}")
        self.assertIsNotNone(d["ended_ms"])
        xs = sorted(sh["x_mm"] for x in d["series"] for sh in x["scored"]["shots"])
        self.assertEqual(len(xs), 3, xs)

    def test_signing_in_mid_match_keeps_only_the_shots_after(self):
        fid = self.custom([("sight", 30, 0), ("record", 60, 3)])
        mid = self.m.create("League", fid, [{"device_id": "lane-1", "competitor": "Typed"}], "md", publish=True)
        self.m.start(mid, "md")
        self.shoot("lane-1", "4.00")                          # the match series, before anyone signed in
        self.clock.t += 1000
        s = self.a.pin_sign_in("lane-1", "123456", "2580")
        self.shoot("lane-1", "1.00")
        self.step(1)
        d = self.sent(f"s{s['id']}")
        shots = [sh for x in d["series"] for st in ("scored", "sighter") for sh in x[st]["shots"]]
        self.assertEqual(len(shots), 1, "the same series, but only the shot fired while signed in")
        self.assertEqual(d["series"][0]["match"]["key"], f"m{mid}")

    def test_match_results_carry_the_athlete(self):
        fid = self.custom([("sight", 30, 0), ("record", 60, 3)])
        self.a.pin_sign_in("lane-1", "123456", "2580")
        mid = self.m.create("League", fid, [{"device_id": "lane-1"}, {"device_id": "lane-2", "competitor": "Bob"}], "md", publish=True)
        self.m.start(mid, "md")
        self.shoot("lane-1"); self.shoot("lane-2")
        self.step(1)
        rows = {e["name"]: e.get("athlete_id") for cl in self.sent(f"m{mid}")["classes"] for e in cl["individuals"]}
        self.assertEqual(rows, {"J. S.": 41, "Bob": None})

    def test_lanes_go_up_for_coaches_while_someone_is_signed_in(self):
        self.step(3)
        self.assertEqual(self.fake.lane_posts, [], "board off and nobody signed in: nothing")
        self.a.pin_sign_in("lane-1", "123456", "2580")
        self.step(3)
        lanes = {x["lane"]: x for x in self.fake.lane_posts[-1]}
        self.assertEqual(lanes[self.target()["lane"]]["athlete_id"], 41)
        self.assertNotIn("athlete_id", lanes[self.target("lane-2")["lane"]])

    def test_unlinking_signs_everyone_out(self):
        self.a.pin_sign_in("lane-1", "123456", "2580")
        self.dt.unlink()
        self.assertIsNone(self.target()["athlete"])
        self.assertEqual(self.core.db.conn().execute("SELECT end_why FROM athlete_sessions").fetchone()[0], "unlinked")


from tests.test_match_http import MatchHttpBase  # noqa: E402


class OverHttp(MatchHttpBase):
    """The lane's screen (PIN, sign out), and staff signing a range athlete in and out from Range or Match Control."""
    def setUp(self):
        super().setUp()
        self.fake = Fake()
        self.fake.athletes = [dict(AL, pin=None)]
        self.addCleanup(self.fake.close)
        self.dt = DeepTens(self.core, self.dir, clock=lambda: self.clock.t / 1000, name=lambda: "Club Pi")
        self.server.deeptens = self.core.deeptens = self.dt
        self.dt.start_link(self.fake.url)
        self.fake.approved = True
        self.dt.next_poll = 0
        self.dt.step()
        self.dt.step()                                       # the roster

    def athlete(self, did="lane-1"):
        st, body, _ = self.req("GET", "/api/v1/targets", header=False)
        return next(t for t in body["targets"] if t["device_id"] == did)["athlete"]

    def test_the_lanes_screen(self):
        st, body, _ = self.req("POST", "/api/v1/targets/lane-1/signin", {"number": "123456", "pin": "0000"}, header=False)
        self.assertEqual((st, body["error"]), (400, "wrong athlete number or PIN"))
        st, body, _ = self.req("POST", "/api/v1/targets/lane-1/signin", {"number": "123456", "pin": "2580"}, header=False)
        self.assertEqual((st, body["athlete"]["athlete_id"]), (200, 41))
        self.assertEqual(self.athlete(), {"public_name": "J. S.", "via": "pin", "started_ms": self.athlete()["started_ms"]},
                         "the lanes' screen (anyone on the network): the public name only")
        st, body, _ = self.req("POST", "/api/v1/targets/lane-1/signout", {}, header=False)
        self.assertEqual((st, body["ended"]["end_why"]), (200, "signout"))
        self.assertIsNone(self.athlete())

    def test_staff_from_range_control_and_match_control(self):
        st, _, _ = self.req("POST", "/api/v1/admin/targets/lane-1/athlete", {"athlete_id": 42})
        self.assertEqual(st, 401, "Range Control's password first")
        st, _, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["rc"] = cookie.split(";")[0]
        st, body, _ = self.req("GET", "/api/v1/admin/athletes", who="rc")
        self.assertEqual(body["athletes"], [{"athlete_id": 42, "number": "654321", "name": "Al Aim", "public_name": "Al Aim"}], "no PIN hashes")
        st, body, _ = self.req("POST", "/api/v1/admin/targets/lane-1/athlete", {"athlete_id": 41}, who="rc")
        self.assertEqual((st, body["error"]), (400, "not one of this range's athletes (a Range Manager adds them on deepTENS)"))
        st, body, _ = self.req("POST", "/api/v1/admin/targets/lane-1/athlete", {"athlete_id": 42}, who="rc")
        self.assertEqual((st, self.athlete()["via"]), (200, "staff"))
        self.login("ro", "officer-pass")
        st, body, _ = self.req("POST", "/api/v1/match/targets/lane-1/athlete", {"athlete_id": None}, who="ro")
        self.assertEqual(st, 200)
        self.assertIsNone(self.athlete())
        st, body, _ = self.req("POST", "/api/v1/match/targets/lane-2/athlete", {"athlete_id": 42}, who="ro")
        self.assertEqual(self.athlete("lane-2")["public_name"], "Al Aim")
        st, body, _ = self.req("GET", "/api/v1/match/targets", who="ro")
        self.assertEqual(next(t for t in body["targets"] if t["device_id"] == "lane-2")["athlete"]["athlete_id"], 42, "Match Control is staff")
