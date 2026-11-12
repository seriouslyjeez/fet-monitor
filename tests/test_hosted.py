"""Hosted virtual matches on the Pi (fetserver/hosted.py, deepTENS P4 stage 5): learning them from deepTENS (and keeping
them through an outage), putting entrants on lanes (by the Match Director or a sign-in), a synchronized match starting
by itself at deepTENS's time and taking late entrants, the clock no one here can change, supervision from staff having
the match open, and what goes to deepTENS: the targets with their keys, the lanes' signed lines (once each), and each
lane's run instead of a match result."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from fetcore import matchformats as mf
from fetserver.core import Core
from fetserver.db import Database
from fetserver.deeptens import DeepTens
from fetserver.hosted import CONSOLE_GAP_MS, run_ms
from fetserver.matches import MatchError
from tests.test_deeptens_link import FakeDeepTens
from tests.test_target_server import KEY, hello, shot, signed

T0 = 1_800_000_000_000
MIN = 60_000
FMT = next(f for f in mf.builtin_list() if f["id"] == "issf-ar60")


def athlete(n):
    return {"athlete_id": n, "number": str(100000 + n), "name": f"Athlete {n}", "public_name": f"A{n}"}


def entrant(n, runs=0, allowed=1):
    return {"entry_id": 500 + n, "athlete": athlete(n), "class": "", "runs": runs, "allowed_runs": allowed}


def sync_match(code="syncmatch1", start=T0 + 60 * MIN, late=10 * MIN, entrants=(1, 2, 3)):
    return {"code": code, "name": "Thanksgiving Shoot", "timing": "sync", "format": copy.deepcopy(FMT), "fetcore_version": "0.2.0",
            "starts_ms": start, "late_ms": late, "opens_ms": None, "closes_ms": None, "attempts": 1, "counts": "best",
            "classes": [], "official": False, "entrants": [entrant(n) for n in entrants]}


def window_match(code="windowmat1", opens=T0 - MIN, closes=T0 + 3 * 86400_000):
    return {"code": code, "name": "Postal League", "timing": "window", "format": copy.deepcopy(FMT), "fetcore_version": "0.2.0",
            "starts_ms": None, "late_ms": None, "opens_ms": opens, "closes_ms": closes, "attempts": 1, "counts": "best",
            "classes": [], "official": False, "entrants": [entrant(9)]}


class Fake(FakeDeepTens):
    def __init__(self):
        super().__init__()
        self.hosted, self.hosted_rev = [], ""
        self.targets, self.lines, self.reject_lines = [], [], set()

    def handle(self, path, body, auth):
        if path == "/api/monitor/v1/heartbeat" and auth == "Bearer " + self.token and self.linked and not self.down:
            st, out = super().handle(path, body, auth)
            return st, dict(out, hosted_rev=self.hosted_rev)
        if path in ("/api/monitor/v1/hosted", "/api/monitor/v1/targets", "/api/monitor/v1/lines"):
            if self.down:
                return self.down, {}
            if auth != "Bearer " + self.token or not self.linked:
                return 401, {}
            if path.endswith("/hosted"):
                return 200, {"matches": self.hosted}
            if path.endswith("/targets"):
                self.targets.append(body["targets"])
                return 200, {"ok": [t["device_id"] for t in body["targets"]], "refused": []}
            acked, rejected = [], []
            for ln in body["lines"]:
                if ln["seq"] in self.reject_lines:
                    rejected.append({"device_id": ln["device_id"], "seq": ln["seq"], "why": "not one of this range's targets on deepTENS"})
                    continue
                self.lines.append(ln)
                acked.append([ln["device_id"], ln["boot_count"], ln["seq"]])
            return 200, {"acked": acked, "rejected": rejected}
        return super().handle(path, body, auth)


class Clock:
    def __init__(self):
        self.ms = T0

    def __call__(self):
        return self.ms


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clock = Clock()
        self.core = Core(Database(str(self.dir / "fet.db")), now_ms=self.clock, lease_ms=10 ** 9)
        self.mc, self.h = self.core.matches, self.core.hosted
        self.seq = {}
        self.core.add_target_key(KEY, "test")
        for did in ("t1", "t2", "t3"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})
            if did != "t2":                               # t2: the original firmware, no key
                self.send(did, "target_hello", hello(3))
        self.fake = Fake()
        self.addCleanup(self.fake.close)
        self.dt = self.new()
        self.core.deeptens = self.dt
        self.dt.start_link(self.fake.url)
        self.fake.approved = True
        self.clock.ms += 5000
        self.dt.step()
        self.assertTrue(self.dt.linked)

    def new(self):
        return DeepTens(self.core, self.dir, clock=lambda: self.clock.ms / 1000, name=lambda: "Club Pi", version="1.1.0")

    def send(self, did, etype, raw, at=None):
        at = at if at is not None else self.clock.ms
        n = self.seq[did] = self.seq.get(did, 0) + 1
        self.core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": at - T0 + 10_000, "events": [
            {"seq": n, "type": etype, "uptime_ms": at - T0 + 10_000, "raw": raw}]})

    def host(self, *matches):
        self.revs = getattr(self, "revs", 0) + 1
        self.fake.hosted, self.fake.hosted_rev = list(matches), f"rev{self.revs}"
        self.dt.next_hb = 0
        self.dt.step()

    def to(self, ms):
        self.clock.ms = ms
        self.h.tick()
        self.mc.tick()

    def rounds(self, n=5):
        for _ in range(n):
            self.dt.step()


class Learning(Case):
    def test_fetched_when_deeptens_says_and_kept_through_an_outage(self):
        self.assertEqual(self.h.by_code, {})
        self.host(sync_match())
        self.assertEqual(list(self.h.by_code), ["syncmatch1"])
        self.assertTrue((self.dir / "deeptens-hosted.json").exists())
        self.fake.down = 503
        again = self.new()
        self.core.hosted.set_matches([])
        again._load_hosted()
        self.assertEqual(list(self.core.hosted.by_code), ["syncmatch1"], "a restart without the internet keeps them")

    def test_the_list_for_match_control(self):
        self.host(sync_match(), window_match())
        got = {x["code"]: x for x in self.h.list()}
        self.assertEqual(set(got), {"syncmatch1", "windowmat1"})
        self.assertEqual([e["left"] for e in got["syncmatch1"]["entrants"]], [1, 1, 1])
        self.assertEqual(got["syncmatch1"]["run_ms"], run_ms(FMT))

    def test_unlinking_forgets_them(self):
        self.host(sync_match())
        self.dt.unlink()
        self.assertEqual(self.h.by_code, {})
        self.assertFalse((self.dir / "deeptens-hosted.json").exists())


class Lanes(Case):
    def setUp(self):
        super().setUp()
        self.host(sync_match())
        self.T = T0 + 60 * MIN

    def test_from_thirty_minutes_before(self):
        self.clock.ms = self.T - 31 * MIN
        with self.assertRaisesRegex(MatchError, "30 minutes before"):
            self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.clock.ms = self.T - 30 * MIN
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        d = self.mc.detail(mid)
        self.assertEqual((d["state"], d["hosted_code"], d["format"]["id"]), ("ready", "syncmatch1", "issf-ar60"))
        ln = self.core.db.conn().execute("SELECT * FROM match_lanes WHERE match_id=?", (mid,)).fetchone()
        self.assertEqual((ln["device_id"], ln["entry_id"], ln["competitor"]), ("t1", 501, "A1"))
        self.assertEqual(self.h.place("syncmatch1", [{"device_id": "t3", "entry_id": 502}], "dir"), mid, "into the same match")

    def test_refusals(self):
        self.clock.ms = self.T - 10 * MIN
        for lanes, why in (([{"device_id": "t2", "entry_id": 501}], "FeT target firmware"),
                           ([{"device_id": "t1", "entry_id": 999}], "an entrant of this match"),
                           ([{"device_id": "t1", "entry_id": 501}, {"device_id": "t3", "entry_id": 501}], "each entrant and each target once"),
                           ([], "at least one")):
            with self.assertRaisesRegex(MatchError, why):
                self.h.place("syncmatch1", lanes, "dir")
        with self.assertRaisesRegex(MatchError, "does not host"):
            self.h.place("nosuchcode", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        with self.assertRaisesRegex(MatchError, "on a lane already"):
            self.h.place("syncmatch1", [{"device_id": "t3", "entry_id": 501}], "dir")
        m = sync_match()
        m["entrants"][1]["runs"] = 1
        self.host(m)
        with self.assertRaisesRegex(MatchError, "no run left"):
            self.h.place("syncmatch1", [{"device_id": "t3", "entry_id": 502}], "dir")

    def test_official_results(self):
        m = sync_match()
        m["official"] = True
        self.host(m)
        self.clock.ms = self.T - 10 * MIN
        with self.assertRaisesRegex(MatchError, "official"):
            self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")

    def test_starts_by_itself_at_deeptens_time_and_takes_late_entrants(self):
        self.clock.ms = self.T - 10 * MIN
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T - 1)
        self.assertEqual(self.mc.detail(mid)["state"], "ready")
        self.to(self.T + 700)                          # the tick comes a moment late: the start is deepTENS's
        self.assertEqual(self.mc.detail(mid)["state"], "running")
        self.assertEqual(self.h.started_ms(self.core.db.conn(), self.core.db.conn().execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()), self.T)
        self.to(self.T + 9 * MIN)
        self.assertEqual(self.h.place("syncmatch1", [{"device_id": "t3", "entry_id": 502}], "dir"), mid, "a late entrant joins it")
        self.assertIn("t3", [ln["device_id"] for ln in self.mc.detail(mid)["lanes"]])
        self.to(self.T + 10 * MIN)
        with self.assertRaisesRegex(MatchError, "too late"):
            self.h.place("syncmatch1", [{"device_id": "t2", "entry_id": 503}], "dir")

    def test_no_one_here_changes_its_clock(self):
        self.clock.ms = self.T - 10 * MIN
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T + MIN)
        for action in ("pause", "extra", "next", "abort", "interrupt"):
            with self.assertRaisesRegex(MatchError, "deepTENS match", msg=action):
                self.mc.command(mid, action, "ro", seconds=60)
        with self.assertRaisesRegex(MatchError, "deepTENS match"):
            self.mc.restart(mid, 0, "dir")
        with self.assertRaisesRegex(MatchError, "deepTENS match"):
            self.mc.move(mid, "t1", "t3", "dir")
        with self.assertRaisesRegex(MatchError, "deepTENS match"):
            self.mc.lane_command(mid, "t1", "extra", "ro", seconds=60)
        with self.assertRaisesRegex(MatchError, "entrants list"):
            self.mc.add_late(mid, "t3", "Someone", "dir")

    def test_a_signed_in_entrant_goes_on_the_lane(self):
        self.clock.ms = self.T - 10 * MIN
        self.core.athletes.sign_in("t3", athlete(2), "pin")
        ln = self.core.db.conn().execute("SELECT * FROM match_lanes WHERE device_id='t3'").fetchone()
        self.assertEqual(ln["entry_id"], 502)
        self.core.athletes.sign_in("t1", athlete(77), "pin")    # not an entrant: nothing
        self.assertIsNone(self.core.db.conn().execute("SELECT * FROM match_lanes WHERE device_id='t1'").fetchone())


class Window(Case):
    def test_a_relay_the_match_director_starts(self):
        self.host(window_match())
        mid = self.h.place("windowmat1", [{"device_id": "t1", "entry_id": 509}], "dir")
        self.to(T0 + 5 * MIN)
        self.assertEqual(self.mc.detail(mid)["state"], "ready", "a window relay waits for the Match Director")
        self.mc.start(mid, "dir")
        (key, data), = self.h.runs(mid)
        self.assertEqual((data["started_ms"], data["ends_ms"]), (T0 + 5 * MIN, T0 + 5 * MIN + run_ms(FMT)))

    def test_a_run_must_end_before_the_window_closes(self):
        self.host(window_match(closes=T0 + run_ms(FMT) - 1))
        with self.assertRaisesRegex(MatchError, "within the match's window"):
            self.h.place("windowmat1", [{"device_id": "t1", "entry_id": 509}], "dir")


class Supervision(Case):
    def setUp(self):
        super().setUp()
        self.host(sync_match())
        self.T = T0 + 60 * MIN
        self.clock.ms = self.T - 10 * MIN
        self.mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T)
        self.end = self.T + run_ms(FMT)

    def seen(self, a, b, every=5000, role="officer"):
        t = a
        while t <= b:
            self.clock.ms = t
            self.h.console_seen(self.mid, role)
            t += every

    def gaps(self):
        return self.h.unsupervised(self.core.db.conn(), self.mid, self.T, self.end)

    def test_staff_throughout(self):
        self.seen(self.T, self.end)
        self.assertEqual(self.gaps(), [])

    def test_nobody(self):
        self.assertEqual(self.gaps(), [[self.T, self.end]])
        self.to(self.end + 1)
        (key, data), = self.h.runs(self.mid)
        self.assertEqual((data["supervised"], data["unsupervised"]), (False, [[self.T, self.end]]))

    def test_a_moment_late_or_early_is_no_gap(self):
        self.seen(self.T + CONSOLE_GAP_MS - 1000, self.end - CONSOLE_GAP_MS + 1000, every=1000)
        self.assertEqual(self.gaps(), [])
        self.clock.ms = self.T
        self.core.db.conn()
        with self.core.db.write() as c:
            c.execute("DELETE FROM hosted_console")
        self.seen(self.T + CONSOLE_GAP_MS, self.end - CONSOLE_GAP_MS, every=1000)
        self.assertEqual(self.gaps(), [[self.T, self.T + CONSOLE_GAP_MS], [self.end - CONSOLE_GAP_MS, self.end]], "a minute: a gap")

    def test_a_gap(self):
        self.seen(self.T, self.T + 10 * MIN)
        self.seen(self.T + 10 * MIN + CONSOLE_GAP_MS - 5000, self.T + 20 * MIN)     # away under a minute: no gap
        self.assertEqual(self.gaps(), [[self.T + 20 * MIN, self.end]])
        self.seen(self.T + 25 * MIN, self.end)
        self.assertEqual(self.gaps(), [[self.T + 20 * MIN, self.T + 25 * MIN]])

    def test_only_staff_count(self):
        self.seen(self.T, self.end, role="spectator")
        self.assertEqual(self.gaps(), [[self.T, self.end]])

    def test_in_the_run_data(self):
        self.seen(self.T, self.end)
        self.to(self.end + 1)
        (key, data), = self.h.runs(self.mid)
        self.assertEqual(key, f"h{self.mid * 1000 + 1}")
        self.assertEqual((data["match"], data["entry_id"], data["device_id"], data["started_ms"], data["ends_ms"], data["supervised"],
                          data["unsupervised"]), ("syncmatch1", 501, "t1", self.T, self.end, True, []))


class ToDeepTens(Case):
    def setUp(self):
        super().setUp()
        self.host(sync_match())
        self.T = T0 + 60 * MIN
        self.clock.ms = self.T - 10 * MIN

    def test_targets_with_their_keys(self):
        self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.rounds()
        self.assertEqual(len(self.fake.targets), 1, "once")
        (t,), = self.fake.targets
        self.assertEqual((t["device_id"], t["key_hex"], t["key_fp"]), ("t1", KEY.hex(), self.core.db.conn().execute(
            "SELECT key_fp FROM targets WHERE device_id='t1'").fetchone()[0]))

    def test_the_lanes_lines_once_each(self):
        self.send("t1", "target_shot", shot(1, (5000, 5000, 5000, 5000)), at=self.T - 5 * MIN)   # practice long before: not sent
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T + MIN)
        self.send("t1", "target_shot", shot(2, (5000, 5100, 5000, 4900)))
        self.send("t3", "target_shot", shot(3, (5000, 5100, 5000, 4900)))                      # not in the match
        self.rounds()
        self.assertEqual([(x["device_id"], x["raw"]) for x in self.fake.lines], [("t1", shot(2, (5000, 5100, 5000, 4900)))])
        x = self.fake.lines[0]
        ev = self.core.db.conn().execute("SELECT event_ms, time_quality FROM events WHERE device_id='t1' AND seq=?", (x["seq"],)).fetchone()
        self.assertEqual((x["event_ms"], x["time_quality"]), (ev["event_ms"], ev["time_quality"]), "the Pi's own time and its quality")
        self.rounds()
        self.assertEqual(len(self.fake.lines), 1, "acknowledged: not again")
        self.assertEqual(mid, mid)

    def test_a_line_for_a_target_not_on_deeptens_yet_is_sent_again(self):
        self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T + MIN)
        self.send("t1", "target_shot", shot(2, (5000, 5100, 5000, 4900)))
        self.fake.reject_lines = {self.seq["t1"]}
        self.rounds(2)
        self.assertEqual(self.fake.lines, [])
        self.fake.reject_lines = set()
        self.rounds(2)
        self.assertEqual(len(self.fake.lines), 1)

    def test_runs_not_results(self):
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T + MIN)
        self.rounds()
        keys = [k for k, _ in self.fake.received]
        self.assertIn(f"h{mid * 1000 + 1}", keys)
        self.assertNotIn(f"m{mid}", keys, "deepTENS scores a hosted match itself")
        data = self.fake.stored[f"h{mid * 1000 + 1}"][1]
        self.assertEqual((data["match"], data["entry_id"], data["started_ms"]), ("syncmatch1", 501, self.T))

    def test_the_athletes_switches_go_with_the_run(self):
        m = sync_match()
        m["format"] = copy.deepcopy(next(f for f in mf.builtin_list() if f["id"] == "nra-s40"))
        self.host(m)
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        self.to(self.T + MIN)
        self.mc.lane_command(mid, "t1", "start_record", "ro")
        (key, data), = self.h.runs(mid)
        self.assertEqual([(a["kind"], a["phase"]) for a in data["actions"]], [("start_record", 0)])

    def test_back_to_sighting_goes_too(self):
        m = sync_match()
        m["format"] = copy.deepcopy(next(f for f in mf.builtin_list() if f["id"] == "issf-r3p-in"))
        self.host(m)
        mid = self.h.place("syncmatch1", [{"device_id": "t1", "entry_id": 501}], "dir")
        rec = self.T + sum(p["seconds"] for p in m["format"]["phases"][:3]) * 1000
        self.to(rec + MIN)
        with self.core.db.write() as c:                  # (as after the kneeling shots: Run.lane_command's own rules are fetcore's)
            self.mc._log(c, mid, rec + MIN, "to_sighters", 3, "t1", {"by": "ro"})
        (key, data), = self.h.runs(mid)
        self.assertEqual([(a["kind"], a["phase"]) for a in data["actions"]], [("to_sighters", 3)])


if __name__ == "__main__":
    unittest.main()


from tests.test_match_http import BASE as HBASE, MatchHttpBase  # noqa: E402


class Http(MatchHttpBase):
    def test_the_list_and_putting_on_a_lane(self):
        self.core.add_target_key(KEY, "test")
        self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 10, "events": [
            {"seq": 1, "type": "target_hello", "uptime_ms": 10, "raw": hello(3)}]})
        self.core.hosted.set_matches([sync_match(start=HBASE + 20 * MIN)])
        self.assertEqual(self.req("GET", "/api/v1/match/hosted")[0], 401, "Match Control staff only")
        self.login("ro", "officer-pass")
        self.login("md", "director-pass")
        st, body, _ = self.req("GET", "/api/v1/match/hosted", who="ro")
        self.assertEqual((st, [h["code"] for h in body["hosted"]]), (200, ["syncmatch1"]))
        lanes = {"lanes": [{"device_id": "lane-1", "entry_id": 501}]}
        self.assertEqual(self.req("POST", "/api/v1/match/hosted/syncmatch1/lanes", lanes, who="ro")[0], 403, "the Match Director's")
        st, d, _ = self.req("POST", "/api/v1/match/hosted/syncmatch1/lanes", lanes, who="md")
        self.assertEqual((st, d["hosted_code"], d["state"]), (200, "syncmatch1", "ready"))
        st, d, _ = self.req("POST", "/api/v1/match/hosted/syncmatch1/lanes", {"lanes": [{"device_id": "lane-2", "entry_id": 502}]}, who="md")
        self.assertEqual(st, 400)
        self.assertIn("FeT target firmware", d["error"])
        # staff with it open: noted for supervision
        self.req("GET", f"/api/v1/match/matches/{d.get('id') or self.core.db.conn().execute('SELECT id FROM matches').fetchone()[0]}", who="ro")
        self.assertEqual(self.core.db.conn().execute("SELECT COUNT(*) FROM hosted_console").fetchone()[0], 1)
