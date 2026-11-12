"""Shots that may not be one clean shot: a double load, an echo, the paper drive, noise (2026-10-31, freETarget 4.3.0
at V_REF 0.48 V). A shot line with "x":NAN is kept as heard-but-not-placed (it counts nothing until a match director
decides); a report within 3 s of another, or four microphone times that disagree, is flagged in a match and marked
in practice. The lines below are real ones from that day (only the shot numbers change)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from fetcore.lineparse import parse_shot_line
from fetserver.matches import CLOSE_MS, MatchError
from tests.test_match_http import BASE as HTTP_BASE, MatchHttpBase
from tests.test_matches import BASE, MatchCase

GOOD = ('{"shot":%d, "miss":0, "name":"TARGET", "time":9223.94 ,"x":-9.03, "y":13.70 , "N":0, "E":325, "S":742, "W":476 , '
        '"V_REF":0.48, , "T":20.50, , "VERSION":"4.3.0 December 21, 2023" }')                       # misfit about 0.1 mm
GOOD2 = ('{"shot":%d, "miss":0, "name":"TARGET", "time":9275.62 ,"x":3.28, "y":12.42 , "N":114, "E":0, "S":411, "W":512 , '
         '"V_REF":0.48, , "T":20.50, , "VERSION":"4.3.0 December 21, 2023" }')
SPLIT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":9253.69 ,"x":-6.77, "y":7.41 , "N":16, "E":0, "S":480, "W":21 , '
         '"V_REF":0.48, , "T":20.50, , "VERSION":"4.3.0 December 21, 2023" }')                       # misfit 5.7 mm
NAN = ('{"shot":%d, "miss":0, "name":"TARGET", "time":9253.72 ,"x":NAN, "y":NAN , "N":1321, "E":12639, "S":0, "W":2042 , '
       '"V_REF":0.48, , "T":20.50, , "VERSION":"4.3.0 December 21, 2023" }')
ECHO = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":0, "S":0, "W":14272 , "face":0 }'   # the paper drive?


class Parse(unittest.TestCase):
    def test_nan_position_is_heard_not_placed(self):
        sl = parse_shot_line(NAN % 42)
        self.assertEqual((sl.shot, sl.miss, sl.unplaced, sl.x_h, sl.y_h, sl.e), (42, False, True, None, None, 12639))
        for v in ("nan", "-inf", "INF", "+NaN"):
            self.assertTrue(parse_shot_line('{"shot":1, "miss":0, "x":%s, "y":1.0}' % v).unplaced, v)
        good = parse_shot_line(GOOD % 39)
        self.assertEqual((good.unplaced, good.x_h, good.y_h), (False, -903, 1370))
        self.assertFalse(parse_shot_line(ECHO % 40).unplaced, "a miss stays a miss")
        named = parse_shot_line('{"shot":1, "miss":0, "name":"a: nan, b", "x":1, "y":2}')
        self.assertEqual((named.name, named.unplaced), ("a: nan, b", False), "text in a string is never touched")
        with self.assertRaises(ValueError):
            parse_shot_line('{"shot":1, "miss":0, "x":banana, "y":1}')
        with self.assertRaises(ValueError):
            parse_shot_line('{"shot":1, "miss":0, "x":nanny, "y":1}')


class Case(MatchCase):
    def setUp(self):
        super().setUp()
        self.m.close_ms = CLOSE_MS

    def send(self, raw, did="lane-1"):
        n = self.seq[did] = self.seq.get(did, 0) + 1
        self.core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": self.clock.t - BASE, "events": [
            {"seq": n, "type": "shot", "uptime_ms": self.clock.t - BASE, "raw": raw % n}]})

    def q(self, sql, *args):
        return self.core.db.conn().execute(sql, args).fetchall()


class Ingest(Case):
    def test_unplaced_is_kept_apart_from_the_shots(self):
        self.at(10); self.send(GOOD)
        self.at(10.03); self.send(NAN)
        self.assertEqual(len(self.q("SELECT * FROM shots")), 1, "never a shot with a value, never a miss")
        u = self.q("SELECT * FROM unplaced")
        self.assertEqual([(r["shot_number"], r["e"], r["stream"], r["decision"]) for r in u], [(2, 12639, "scored", None)])
        self.assertEqual(u[0]["series_id"], self.q("SELECT series_id FROM shots")[0]["series_id"], "in the series it was fired in")
        self.assertEqual([r["parse_error"] for r in self.q("SELECT parse_error FROM events ORDER BY id")], [None, None])

    def test_misfit_of_freetarget_shots(self):
        self.at(10); self.send(GOOD)
        self.at(30); self.send(SPLIT)
        self.at(50); self.send(ECHO)
        got = [r["misfit_um"] for r in self.q("SELECT misfit_um FROM shots ORDER BY id")]
        self.assertLess(got[0], 500)
        self.assertAlmostEqual(got[1], 5724, delta=50, msg="the microphones disagree: 5.7 mm (as measured 2026-10-31)")
        self.assertIsNone(got[2], "a miss has no position to check")

    def test_rescore_recovers_old_refusals_once(self):
        self.at(10); self.send(GOOD)
        with self.core.db.write() as c:                     # as an older server left them
            c.execute("UPDATE shots SET misfit_um=NULL")
            c.execute("INSERT INTO events (device_id, boot_count, seq, type, event_ms, time_quality, recv_ms, uptime_ms, raw, parse_error) "
                      "VALUES ('lane-1', 1, 99, 'shot', ?, 'ok', ?, 0, ?, 'not parseable as JSON')", (BASE + 10_030, BASE + 10_030, NAN % 99))
            c.execute("INSERT INTO events (device_id, boot_count, seq, type, event_ms, time_quality, recv_ms, uptime_ms, raw, parse_error) "
                      "VALUES ('lane-1', 1, 98, 'shot', ?, 'ok', ?, 0, 'garbage', 'not parseable as JSON')", (BASE + 10_040, BASE + 10_040))
        self.core.rescore()
        self.core.rescore()
        self.assertEqual([r["shot_number"] for r in self.q("SELECT shot_number FROM unplaced")], [99], "once, however often")
        self.assertEqual([r["raw"] for r in self.q("SELECT raw FROM events WHERE parse_error IS NOT NULL")], ["garbage"])
        self.assertLess(self.q("SELECT misfit_um FROM shots")[0]["misfit_um"], 500, "filled in for older shots")


class MatchFlags(Case):
    def start(self, shots=2):
        mid = self.m.create("M", self.custom([("record", 600, shots)]), [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "dir")
        self.m.start(mid, "ro")
        return mid

    def total(self, mid):
        return [(x["tenths"], x["miss"]) for x in self.m.counted(self.core.db.conn(), self.m._row(self.core.db.conn(), mid),
                                                                  self.m._lanes(self.core.db.conn(), mid)[0], {f["shot_id"]: f for f in self.m.detail(mid)["flags"]})]

    def test_echo_after_a_shot(self):
        mid = self.start(2)
        self.at(10); self.send(GOOD)
        self.at(11.5); self.send(ECHO)                       # the paper drive, 1.5 s later
        self.at(40); self.send(GOOD2)
        f = self.m.detail(mid)["flags"]
        self.assertEqual([(x["n"], x["code"], x["why"]) for x in f], [(2, "close", "1.5 s after shot 1: a double load, an echo or noise?")])
        self.assertEqual(f[0]["pair"]["n"], 1)
        self.assertIsNone(f[0]["guide"]["suggest"], "the director looks at the target: nothing is suggested")
        self.assertEqual(len(self.total(mid)), 2, "undecided, the echo counts nothing and does not use up a record shot")
        self.assertIn("1 flagged shot not decided", self.m.open_items(self.core.db.conn(), self.m._row(self.core.db.conn(), mid)))
        self.m.decide(mid, f[0]["shot_id"], "count", "dir")   # it was a shot after all: shot 3 is now one too many
        got = {x["n"]: (x["code"], x["decision"]) for x in self.m.detail(mid)["flags"]}
        self.assertEqual(got, {2: ("close", "count"), 3: ("too_many", None)})
        self.m.decide(mid, f[0]["shot_id"], "annul", "dir")
        self.assertEqual([x["n"] for x in self.m.detail(mid)["flags"]], [2])
        self.assertEqual(len(self.total(mid)), 2)

    def test_a_shot_well_after_another_is_not_flagged(self):
        mid = self.start(3)
        self.at(10); self.send(GOOD)
        self.at(10 + CLOSE_MS / 1000 + 0.01); self.send(GOOD2)
        self.at(30); self.send(GOOD, "lane-2")              # another firing point at the same moment: not this one's
        self.at(30.5); self.send(GOOD)
        self.assertEqual(self.m.detail(mid)["flags"], [])

    def test_double_load_two_reports_30_ms_apart(self):
        mid = self.start(2)
        self.at(10); self.send(SPLIT)
        self.at(10.03); self.send(NAN)
        d = self.m.detail(mid)
        self.assertEqual([(x["n"], x["code"]) for x in d["flags"]], [(1, "inconsistent")])
        self.assertIn("disagree by 5.7 mm", d["flags"][0]["why"])
        self.assertEqual([(u["lane"], u["why"], u["pair"]["n"], u["decision"]) for u in d["unplaced"]],
                         [(1, "heard but not placed by the target, 0.0 s after shot 1: a double load, an echo or noise?", 1, None)])
        items = self.m.open_items(self.core.db.conn(), self.m._row(self.core.db.conn(), mid))
        self.assertIn("1 shot heard but not placed, not decided", items)
        self.assertEqual(self.total(mid), [], "undecided: neither counts")
        uid = d["unplaced"][0]["id"]
        self.m.decide_unplaced(mid, uid, "miss", "dir")
        self.m.decide(mid, d["flags"][0]["shot_id"], "count", "dir")
        self.assertEqual(sorted(self.total(mid)), [(0, True), (self.q("SELECT tenths FROM shots")[0]["tenths"], False)])
        self.m.decide_unplaced(mid, uid, "ignore", "dir")
        self.assertEqual(len(self.total(mid)), 1)
        self.m.decide_unplaced(mid, uid, "undecided", "dir")
        self.m.decide_unplaced(mid, uid, "ignore", "dir", "an echo from lane 4")
        self.assertEqual(self.m.detail(mid)["unplaced"][0]["note"], "an echo from lane 4")
        self.assertIn("note: an echo from lane 4", next(r["detail"] for r in self.m.incidents(mid)["rows"] if r["what"] == "Shot heard but not placed"))
        self.m.decide_unplaced(mid, uid, "miss", "dir")
        self.assertEqual(self.m.detail(mid)["unplaced"][0]["note"], "an echo from lane 4", "a quick choice keeps the note")
        self.m.decide_unplaced(mid, uid, "undecided", "dir")
        self.assertIsNone(self.m.detail(mid)["unplaced"][0]["note"])
        self.assertIsNone(self.m.detail(mid)["unplaced"][0]["decision"])
        with self.assertRaises(MatchError):
            self.m.decide_unplaced(mid, uid, "count", "dir")
        with self.assertRaises(MatchError):
            self.m.decide_unplaced(mid, uid + 1, "miss", "dir")
        rows = [r for r in self.m.incidents(mid)["rows"] if r["what"] == "Shot heard but not placed"]
        self.assertEqual(len(rows), 1)
        self.assertIn("NOT DECIDED", rows[0]["detail"])

    def test_not_placed_before_the_match_is_not_its_business(self):
        self.at(5); self.send(NAN)                          # practice, before the match was made
        mid = self.start(2)
        self.at(10); self.send(GOOD)
        self.assertEqual(self.m.detail(mid)["unplaced"], [])
        self.assertEqual(self.m.open_items(self.core.db.conn(), self.m._row(self.core.db.conn(), mid)), ["the match has not finished"])


class Practice(Case):
    def test_marked_but_counted_as_fired(self):
        self.at(10); self.send(GOOD)
        self.at(11); self.send(ECHO)
        self.at(30); self.send(SPLIT)
        self.at(30.03); self.send(NAN)
        sid = self.q("SELECT series_id FROM shots")[0]["series_id"]
        v = self.core.series_view(sid)
        self.assertEqual(v["count"], 3, "practice totals are what the target sent; the marks say what to check")
        got = {s["n"]: s["check"] for s in v["bull_shots"]}
        self.assertIsNone(got[1])
        self.assertTrue(got[2].startswith("1.0 s after shot 1"))
        self.assertIn("disagree", got[3])
        self.assertEqual(len(v["unplaced"]), 1)
        self.assertEqual(self.core.series_view(sid, upto=2)["unplaced"], [], "review: not heard yet after shot 2")
        self.assertEqual(len(self.core.series_view(sid, upto=3)["unplaced"]), 0, "heard 30 ms after shot 3")
        self.assertEqual(self.core.series_view(sid, "sighter")["unplaced"], [])


class Http(MatchHttpBase):
    def test_director_decides_a_shot_heard_but_not_placed(self):
        self.login("md", "director-pass"); self.login("ro", "officer-pass")
        st, f, _ = self.req("POST", "/api/v1/match/formats", {"format": {"name": "Rec", "book": "ISSF", "phases": [
            {"kind": "record", "label": "Record", "seconds": 600, "shots": 2}]}}, "md")
        st, m, _ = self.req("POST", "/api/v1/match/matches", {"name": "Club", "format_id": f["id"], "lanes": [{"device_id": "lane-1"}]}, "md")
        mid = m["id"]
        self.req("POST", f"/api/v1/match/matches/{mid}/start", {}, "ro")
        self.clock.t += 10_000
        up = self.clock.t - HTTP_BASE
        self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": up, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": up, "raw": NAN % 1}]})
        st, m, _ = self.req("GET", f"/api/v1/match/matches/{mid}", who="ro")
        uid = m["unplaced"][0]["id"]
        path = f"/api/v1/match/matches/{mid}/unplaced/{uid}"
        self.assertEqual(self.req("POST", path, {"decision": "miss"}, "ro")[0], 403, "the match director decides")
        self.assertEqual(self.req("POST", path, {"decision": "maybe"}, "md")[0], 400)
        st, m, _ = self.req("POST", path, {"decision": "ignore"}, "md")
        self.assertEqual((st, m["unplaced"][0]["decision"]), (200, "ignore"))
        self.assertEqual(self.req("POST", f"/api/v1/match/matches/{mid}/unplaced/{uid + 7}", {"decision": "miss"}, "md")[0], 400)


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class Page(unittest.TestCase):
    def test_flags_panel_and_marks(self):
        js = r"""
const fs = require('fs'), assert = require('assert');
const html = fs.readFileSync(process.argv[1], 'utf8');
const mod = {}; new Function('module', html.match(/<script>([\s\S]*)<\/script>/)[1])(mod);
const {flagsList, flagSheet, checkMark, shotsTable, lastPanel} = mod.exports;
const d = {state: 'running', clock: {label: 'Match'}, lanes: [{device_id: 'l1', lane: 1, target: 'Lane 1', competitor: 'A'}],
           flags: [{shot_id: 7, device_id: 'l1', lane: 1, n: 2, stream: 'scored', miss: true, decimal: 0, why: '1.5 s after shot 1: <b>', code: 'close',
                    pair: {shot_id: 6, n: 1}, decision: null, guide: {rule: null, text: 'Look at the target.', suggest: null, url: ''}}],
           unplaced: [{id: 3, device_id: 'l1', lane: 1, why: 'heard but not placed by the target', decision: 'miss', pair: null}], rb: {}};
const dir = flagsList(d, true), ro = flagsList(d, false);
assert(dir.includes('Flagged shots (2, 1 to decide)'));
assert(dir.includes('data-act="mcflag" data-id="7"') && dir.includes('data-act="mcunpl" data-id="3"') && dir.includes('A shot: scored as a miss'), 'each opens its sheet; the decision shown');
assert(dir.includes('&lt;b&gt;') && !dir.includes('<b>:'), 'escaped');
assert(!ro.includes('data-act') && ro.includes('A shot: scored as a miss'), 'officers see, do not decide');
const sh = flagSheet({status: {role: 'director'}, detail: d, flagp: {shot: 7}});
assert(sh.includes('data-act="mcdouble" data-lane="1" data-shot="7" data-other="6"'), 'one click to the double load form');
assert(/value="miss" data-flagpick="1" checked/.test(flagSheet({status: {role: 'director'}, detail: d, flagp: {unp: 3}})), 'heard, not placed: its decision');
assert(flagsList({state: 'running', flags: [], unplaced: [], rb: {}}, true).includes('too soon after another shot'));
assert.strictEqual(checkMark({check: null}), '');
assert(checkMark({check: 'a "b"'}).includes('title="a &quot;b&quot;"'));
const v = {bull_size: 10, bull_shots: [{n: 1, miss: false, decimal: 10.2, ring: 10, inner: false, check: null},
                                       {n: 2, miss: true, decimal: 0, ring: 0, inner: false, check: '1.0 s after shot 1'}], unplaced: [{id: 1}],
           groups: [], agg: {decimal: 10.2, integer: 10, x: 0}, count: 2, total: 60, last_shot: {n: 2, miss: true, ring: 0, check: '1.0 s after shot 1'}};
const t = shotsTable(v);
assert((t.match(/class="chk"/g) || []).length === 1 && t.includes('1 heard, not placed'));
assert(lastPanel(v).includes('Check the target: 1.0 s after shot 1'));
assert(!shotsTable(Object.assign({}, v, {unplaced: undefined})).includes('heard'), 'older views without the list');
"""
        page = Path(__file__).resolve().parent.parent / "fetserver" / "web" / "dashboard.html"
        r = subprocess.run(["node", "-e", js, str(page)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
