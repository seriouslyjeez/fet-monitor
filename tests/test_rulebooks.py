"""One rulebook per format (rulebooks.py): every procedure, rule reference, screen text and final comes from the
format's own book; a book that does not cover something says the match director decides."""
import re
import unittest

from fetserver import matchformats as mf
from fetserver import rulebooks as rb
from fetserver import rules
from fetserver.matches import MatchError
from tests.test_matches import MatchCase, tiny

CITE = re.compile(r"\b(ISSF|USAS|CMP AG|CMP Air Gun|CMP 3PAR|3PAR|NRA|CMP)\b(?= ?[0-9]+(?:\.|\b))")   # a book, then a rule number
OWN = {rb.ISSF: {"ISSF"}, rb.AG: {"CMP AG"}, rb.PAR: {"CMP 3PAR"}, rb.NRA: {"NRA"}, rb.NONE: set()}


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from strings(v)


class NoCrossing(unittest.TestCase):
    def test_every_book_has_every_procedure(self):
        keys = set(rb.PROC[rb.ISSF])
        for book in rb.BOOKS:
            self.assertEqual(set(rb.PROC[book]), keys, book)

    def test_screen_texts_cite_only_their_own_book(self):
        for book in rb.BOOKS:
            found = {m.group(1) for s in strings(rb.texts(book)) for m in CITE.finditer(s)}
            self.assertLessEqual(found, OWN[book], book)
            if book != rb.NONE:
                self.assertTrue(found, book)

    def test_guidance_cites_only_the_match_book(self):
        codes = [("before_start", None), ("before_cmd", "sight"), ("before_cmd", "record"), ("no_fire", "changeover"), ("after_end", None),
                 ("after_own_time", None), ("sighter_in_record", "record"), ("too_many", "record"), ("paused", "record"),
                 ("not_in_shoot_off", "record"), ("waiting", "record")]
        for book in rb.BOOKS:
            for code, kind in codes:
                for stream in ("scored", "sighter"):
                    g = rules.guidance(code, kind, stream, book)
                    found = {m.group(1) for s in strings(g) for m in CITE.finditer(s)}
                    self.assertLessEqual(found, OWN[book], (book, code, stream, g))
                    if g["rule"] is None and g["text"].startswith(("Not in", "No rulebook")):
                        self.assertIsNone(g["suggest"], (book, code))     # no borrowed suggestion either

    def test_summaries_are_per_book(self):
        self.assertEqual(set(rules.SUMMARIES), {rb.ISSF, rb.AG, rb.PAR, rb.NRA})
        self.assertIsNone(rules.explain("7.17.8", rb.AG)["text"], "a 3PAR number is not looked up in the Air Gun book")
        self.assertIsNone(rules.explain("7.11.8", rb.PAR)["text"])

    def test_builtin_formats_name_their_book(self):
        want = {"issf-ar60": rb.ISSF, "cmp-ag-ar60": rb.AG, "cmp-3x10": rb.PAR, "cmp-3x20": rb.PAR, "cmp-s60": rb.PAR,
                "cmp-s3x20": rb.PAR, "nra-3x20": rb.NRA, "nra-s60": rb.NRA}
        for fid, book in want.items():
            self.assertEqual(mf.BUILTIN[fid]["book"], book, fid)
            self.assertEqual(mf.validate(mf.BUILTIN[fid])["book"], book, fid)
        self.assertEqual(mf.cmp_standard_final()["book"], rb.PAR)
        self.assertEqual(mf.cmp_airgun_final()["book"], rb.AG)
        self.assertEqual(mf.BUILTIN["cmp-ag-ar60"]["edition"], rb.AG_EDITION)
        self.assertEqual(mf.BUILTIN["cmp-s60"]["edition"], rb.PAR_EDITION)

    def test_old_formats_keep_working(self):
        """Formats stored before books were named: "CMP" is the 3PAR rules, "Custom" has none."""
        old = {"name": "x", "phases": [{"kind": "record", "seconds": 60, "shots": 1}]}
        self.assertEqual([rb.book_of(dict(old, rulebook=r)) for r in ("ISSF", "CMP", "CMP Air Gun", "NRA", "Custom", None)],
                         [rb.ISSF, rb.PAR, rb.AG, rb.NRA, rb.NONE, rb.NONE])
        self.assertEqual(rb.tie_of(dict(old, rulebook="Custom", tie_rules="NRA")), "NRA", "no book: the format's own tie-break choice")
        self.assertEqual(rb.tie_of(dict(old, book=rb.AG, tie_rules="NRA")), "CMP", "a book's formats break ties by that book")
        self.assertEqual(mf.validate(dict(old, book=rb.AG, tie_rules="NRA"))["tie_rules"], "CMP")
        self.assertEqual(mf.validate(dict(old, book=rb.NONE, tie_rules="NRA"))["tie_rules"], "NRA")
        with self.assertRaises(ValueError):
            mf.validate(dict(old, book="CMP"))

    def test_nra_borrows_nothing(self):
        """NRA covers a shot that did not register (10.10.5 d) but not double loading or a protest that is not upheld."""
        self.assertEqual([rb.rule(rb.NRA, k) for k in ("not_registered", "not_registered_move", "double_load", "not_upheld", "late", "disturbance")],
                         ["NRA 10.10.5 d", "NRA 10.10.5 d 3", None, None, None, None])
        t = rb.texts(rb.NRA)
        self.assertIn("Not in the NRA rules: the match director decides", t["double_load"])
        self.assertIsNone(t["not_upheld"])
        self.assertEqual(rb.range_interruption(rb.NRA, 300), (0, False, "NRA 9.2 b"), "sighters only after 5 minutes (10.3.2)")

    def test_protest_time(self):
        self.assertEqual([rb.protest_minutes(b) for b in (rb.ISSF, rb.AG, rb.PAR, rb.NRA, rb.NONE)], [10, 10, 10, None, None])
        self.assertTrue(rb.texts(rb.NONE)["challenge"] and rb.texts(rb.NRA)["challenge"] and not rb.texts(rb.AG)["challenge"])


class InAMatch(MatchCase):
    def run_book(self, book, kinds):
        fid = self.m.save_format(dict(tiny(kinds), name="B " + book, book=book), "md")["id"]
        mid = self.m.create("M", fid, [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "md")
        self.m.start(mid, "md")
        return mid

    def test_air_gun_too_many_and_protest_time(self):
        mid = self.run_book(rb.AG, [("record", 600, 2)])
        self.at(10)
        for _ in range(3):
            self.shoot("lane-1")
        flag = self.m.detail(mid)["flags"][0]
        self.assertEqual((flag["guide"]["rule"], flag["guide"]["suggest"]), ("CMP AG 7.11.8", "annul_penalty2"))
        self.m.decide(mid, flag["shot_id"], "annul_penalty2", "md")
        row = next(r for r in self.m.results(mid)["rows"] if r["device_id"] == "lane-1")
        self.assertEqual(row["penalties"][0]["rule"], "CMP AG 7.11.8")
        self.assertIn("first 10-shot series", row["penalties"][0]["text"])
        self.assertEqual(self.m.detail(mid)["rb"]["name"], "CMP Air Gun")

    def test_no_rulebook_suggests_nothing(self):
        mid = self.run_book(rb.NONE, [("record", 600, 1)])
        self.at(10); self.shoot("lane-1"); self.shoot("lane-1")
        g = self.m.detail(mid)["flags"][0]["guide"]
        self.assertEqual((g["rule"], g["suggest"], g["text"]), (None, None, "No rulebook: the match director decides."))
        d = self.m.detail(mid)
        self.assertEqual((d["move_extra_s"], d["rb"]["later_relay"]), (0, None))

    def test_no_rulebook_malfunction_and_interruption(self):
        mid = self.run_book(rb.NONE, [("record", 600, 5)])
        self.at(100); self.m.lane_command(mid, "lane-1", "malfunction", "ro", seconds=1)    # the RO allows sighters
        ln = self.m.detail(mid)["lanes"][0]
        self.assertEqual((ln["own"], ln["sighting"]), ("", True))
        self.m.lane_command(mid, "lane-2", "interrupt", "ro")
        self.at(500); self.m.lane_command(mid, "lane-2", "interrupt_end", "ro")             # 400 s lost: given back, nothing more
        ln = self.m.detail(mid)["lanes"][1]
        self.assertEqual((ln["own_remaining_ms"], ln["sighting"]), (500_000, False))

    def test_air_gun_malfunction_in_the_last_five_minutes(self):
        mid = self.run_book(rb.AG, [("record", 600, 5)])
        self.at(300); self.m.lane_command(mid, "lane-1", "malfunction", "ro")
        ln = self.m.detail(mid)["lanes"][0]
        self.assertEqual((ln["own"], ln["sighting"], ln["own_remaining_ms"]), ("extra", True, 420_000), "300 s left: + 2 min (7.13)")
        self.assertIn("CMP AG 7.13", [r["rule"] for r in self.m.incidents(mid)["rows"]])

    def test_nra_move_gives_five_minutes(self):
        mid = self.run_book(rb.NRA, [("record", 600, 5)])
        self.assertEqual(self.m.detail(mid)["move_extra_s"], 300, "NRA 10.10.5 c")


class SavedFormats(MatchCase):
    def test_a_custom_format_must_name_its_book(self):
        with self.assertRaises(MatchError) as cm:
            self.m.save_format(tiny([("record", 60, 1)]) | {"book": None}, "md")
        no_key = dict(tiny([("record", 60, 1)]))
        del no_key["book"]
        with self.assertRaises(MatchError) as cm:
            self.m.save_format(no_key, "md")
        self.assertIn("rulebook", str(cm.exception))
        f = self.m.save_format(dict(tiny([("record", 60, 1)]), book=rb.NONE, tie_rules="NRA"), "md")
        self.assertEqual((f["book"], f["tie_rules"], f["rulebook"]), (rb.NONE, "NRA", "Custom"))


class CorrectionsByBook(MatchCase):
    def match(self, book, kinds):
        fid = self.m.save_format(dict(tiny(kinds), name="C " + book, book=book), "md")["id"]
        mid = self.m.create("M", fid, [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "md")
        self.m.start(mid, "md")
        return mid

    def later(self, s):
        self.clock.t += s * 1000
        self.m.tick()

    def counted(self, mid, lane):
        c = self.core.db.conn()
        m = self.m._row(c, mid)
        ln = next(l for l in self.m._lanes(c, mid) if l["lane"] == lane)
        return self.m.counted(c, m, ln, {f["shot_id"]: f for f in self.m.detail(mid)["flags"]})

    def test_protest_not_upheld_cites_the_match_book(self):
        for book, want in ((rb.AG, "CMP AG 8.2.3 c"), (rb.PAR, "CMP 3PAR 8.4 c"), (rb.NRA, "protest not upheld"), (rb.NONE, "protest not upheld")):
            mid = self.match(book, [("record", 600, 3)])
            self.at(10); self.shoot("lane-1", "1.00")
            sid = [x for x in self.core.corrections.lane_shots(mid, 1)["shots"] if x["stream"] == "scored"][0]["id"]
            pid = self.core.corrections.add(mid, "protest", 1, "ro", False, sid, {"note": "x"})
            self.core.corrections.settle(mid, pid, "not_upheld", "md", penalty=2)
            self.assertEqual(self.counted(mid, 1)[0]["penalties"], [want], book)
            self.m.command(mid, "abort", "md")

    def test_sighter_crossfire_onto_a_record_target_by_book(self):
        """2 points off the firer only where the book says so (ISSF 6.11.6.3, NRA 10.10.4 a); the CMP books do not."""
        for book, want in ((rb.ISSF, ["ISSF 6.11.6.3"]), (rb.NRA, ["NRA 10.10.4 a"]), (rb.AG, None), (rb.PAR, None), (rb.NONE, None)):
            mid = self.match(book, [("stage", 600, 3)])
            self.m.command(mid, "start_record", "ro", device_id="lane-2")
            self.later(10); self.shoot("lane-2", "0.30")             # lane-1 still sighting: its sighter hits lane-2's record target
            self.later(5); self.m.command(mid, "start_record", "ro", device_id="lane-1")
            self.later(5); self.shoot("lane-1", "0.00")
            hit = [x for x in self.core.corrections.lane_shots(mid, 2)["shots"] if x["stream"] == "scored"][0]["id"]
            self.core.corrections.add(mid, "crossfire", 2, "md", True, hit, {"from_lane": 1})
            self.assertEqual(self.counted(mid, 1)[0].get("penalties"), want, book)
            self.m.command(mid, "abort", "md")

    def test_late_arrival_cites_the_match_book(self):
        self.core.handle_heartbeat({"device_id": "lane-3", "boot_count": 1, "uptime_ms": 0})
        for book, want in ((rb.AG, "CMP AG 7.14"), (rb.PAR, "CMP 3PAR 7.20"), (rb.NRA, "")):
            mid = self.match(book, [("sight", 100, 0), ("record", 300, 2)])
            self.later(150)
            self.m.add_late(mid, "lane-3", "Late", "md")
            self.assertEqual([r["rule"] for r in self.m.incidents(mid)["rows"] if r["what"] == "Late arrival joined"], [want], book)
            self.m.command(mid, "abort", "md")


class FinalsByBook(MatchCase):
    def comp(self, book):
        fid = self.m.save_format(dict(tiny([("record", 100, 2)]), name="Q " + book, book=book), "md")["id"]
        return self.core.competitions.create("League " + book, fid, "md")

    def test_a_final_only_from_its_book(self):
        cc = self.core.competitions
        for book, kind in ((rb.PAR, "airgun"), (rb.PAR, "issf"), (rb.AG, "standard"), (rb.AG, "sfz_standing"), (rb.ISSF, "standard"),
                           (rb.ISSF, "airgun"), (rb.ISSF, "nra"), (rb.NRA, "standard"), (rb.NRA, "issf")):
            with self.assertRaises(MatchError, msg=(book, kind)) as cm:
                cc.create_final(self.comp(book), kind, ["lane-1"], "md")
            self.assertIn("rules have no", str(cm.exception))
        self.assertEqual(rb.finals(rb.PAR), ("standard", "sfz_standing", "sfz_3p"))
        self.assertEqual([rb.finals(b) for b in (rb.AG, rb.ISSF, rb.NRA)], [("airgun",), ("issf",), ("nra",)])
        for book, kind in ((rb.AG, "airgun"), (rb.PAR, "standard"), (rb.PAR, "sfz_standing"), (rb.ISSF, "issf"), (rb.NRA, "nra"),
                           (rb.NONE, "airgun"), (rb.NONE, "standard"), (rb.NONE, "issf")):   # allowed: on to the next check
            with self.assertRaises(MatchError, msg=(book, kind)) as cm:
                cc.create_final(self.comp(book), kind, ["lane-1"], "md")
            self.assertNotIn("rules have no", str(cm.exception))          # refused later: no competitors yet


if __name__ == "__main__":
    unittest.main()
