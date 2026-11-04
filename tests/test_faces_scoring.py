"""Shots scored on the face of their series (a target's installed face): the face's rings, its projectile and its
inner ten, kept with each shot; rescoring keeps each shot's face; shots from before faces keep their inner tens."""
import os
import tempfile
import unittest

from fetserver.core import BadRequest, Core, Forbidden
from fetserver.db import MIGRATIONS, Database
from tests.test_control import SHOT, CtlCase
from tests.test_migrations import build_at

BASE = 1_000_000_000


class OnAFace(CtlCase):
    def face(self, face_id):
        with self.core.db.write() as c:
            c.execute("UPDATE targets SET face_id=? WHERE device_id='a1'", (face_id,))

    def shots(self):
        return self.core.db.conn().execute("SELECT ring, tenths, inner, face_id FROM shots ORDER BY id").fetchall()

    def test_50m_rifle(self):
        self.face("issf-r50")
        self.shoot(1_000, "5.30", "0.00")          # inner ten: touches the 5 mm ring with a 5.6 mm bullet
        self.shoot(2_000, "5.31", "0.00")
        self.shoot(3_000, "8.01", "0.00")          # the ten reaches 5.2 + 2.8 mm
        a, b, c = self.shots()
        self.assertEqual((a["ring"], a["inner"], a["face_id"]), (10, 1, "issf-r50"))
        self.assertEqual((b["ring"], b["inner"]), (10, 0), "a ten, not an inner ten (it would be 10.3 on the air rifle rule)")
        self.assertEqual((c["ring"], c["tenths"]), (9, 99))
        v = self.target()["view"]
        self.assertEqual(v["agg"]["x"], 1)
        self.assertEqual([s["inner"] for s in v["bull_shots"]], [True, False, False])

    def test_air_rifle_by_default(self):
        self.shoot(1_000, "2.00", "0.00")
        self.shoot(2_000, "2.01", "0.00")
        self.assertEqual([(r["tenths"], r["inner"], r["face_id"]) for r in self.shots()], [(102, 1, None), (101, 0, None)])

    def test_a_series_keeps_its_face(self):
        self.shoot(1_000, "5.30", "0.00")         # practice series on the air rifle face
        self.face("issf-r50")                       # changed without a new series: the series stays on its face
        self.shoot(2_000, "5.30", "0.00")
        self.assertEqual([r["face_id"] for r in self.shots()], [None, None])

    def test_sight_clicks_count_this_face_s_rings(self):
        self.face("issf-r50")
        self.shoot(1_000, "1.00", "0.00")
        sid = self.target()["view"]["series_id"]
        self.assertEqual(self.core.sight_suggestion(sid)["ring_mm"], 8.0, "a 50 m rifle ring is 8 mm wide")

    def test_rescore_keeps_each_face(self):
        self.face("nra-a23")
        self.shoot(1_000, "7.79", "0.00")
        with self.core.db.write() as c:
            c.execute("UPDATE shots SET ring=0, tenths=0, inner=0")
        self.core.rescore()
        r = self.shots()[0]
        self.assertEqual((r["ring"], r["inner"], r["face_id"]), (10, 1, "nra-a23"), "an X on the A-23 (0.39 in X ring, .224 gauge)")


class Choosing(CtlCase):
    def series_faces(self):
        return [r[0] for r in self.core.db.conn().execute("SELECT face_id FROM series ORDER BY id")]

    def test_range_control_sets_the_installed_face(self):
        with self.assertRaises(BadRequest):
            self.core.set_face("a1", "issf-banana")
        self.shoot(1_000, "1.00", "0.00")                    # a practice series on the air rifle face, with a shot
        r = self.core.set_face("a1", "issf-r50")
        self.assertEqual(r["face"]["id"], "issf-r50")
        self.assertEqual(self.series_faces(), [None, "issf-r50"], "a series with shots ends; a new one starts on the new face")
        self.assertEqual((self.target()["installed_face"], self.target()["view"]["face"]["id"]), ("issf-r50", "issf-r50"))
        self.core.set_face("a1", "issf-ap10")                # the new series has no shots yet: it simply takes the face
        self.assertEqual(self.series_faces(), [None, "issf-ap10"])

    def test_not_in_a_match_and_a_match_uses_its_format_s_face(self):
        fid = self.core.matches.save_format({"name": "Smallbore", "book": "NONE", "scoring": "decimal", "face": "issf-r50",
                                             "phases": [{"kind": "record", "label": "R", "seconds": 600, "shots": 10}]}, "md")["id"]
        mid = self.core.matches.create("Night", fid, [{"device_id": "a1"}], "md")
        with self.assertRaisesRegex(Forbidden, "match"):
            self.core.set_face("a1", "issf-ap10")
        d = self.core.matches.detail(mid)
        self.assertEqual((d["face"]["id"], d["lanes"][0]["installed_face"]), ("issf-r50", "issf-ar10"), "the mask still needs swapping")
        self.core.matches.start(mid, "md")
        self.assertEqual(self.series_faces()[-1], "issf-r50", "the lane's match series is on the match's face")

    def test_formats_name_a_face(self):
        from fetserver.matchformats import validate
        base = {"name": "X", "book": "NONE", "phases": [{"kind": "record", "label": "R", "seconds": 600, "shots": 10}]}
        self.assertEqual(validate(dict(base))["face"], "issf-ar10", "the 10 m air rifle face unless said")
        with self.assertRaisesRegex(ValueError, "face"):
            validate(dict(base, face="nope"))
        with self.assertRaisesRegex(ValueError, "whole rings"):
            validate(dict(base, face="nra-a17", scoring="decimal"))
        self.assertEqual(validate(dict(base, face="nra-a17", scoring="integer"))["face"], "nra-a17")


class Backfill(unittest.TestCase):
    def test_inner_tens_from_before_faces(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            conn = build_at(path, 24)
            conn.execute("INSERT INTO targets (device_id, first_seen_ms, last_seen_ms) VALUES ('a1', ?, ?)", (BASE, BASE))
            for i, (miss, tenths) in enumerate(((0, 102), (0, 101), (1, 0), (0, 109))):
                eid = conn.execute("INSERT INTO events (device_id, boot_count, seq, type, event_ms, time_quality, recv_ms) "
                                   "VALUES ('a1', 1, ?, 'shot', ?, 'ok', ?)", (i, BASE, BASE)).lastrowid
                conn.execute("INSERT INTO shots (event_id, device_id, event_ms, is_miss, scoring_version, ring, tenths) "
                             "VALUES (?, 'a1', ?, ?, 1, ?, ?)", (eid, BASE, miss, tenths // 10, tenths))
            conn.commit()
            conn.close()
            db = Database(path)
            self.assertEqual(db.conn().execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
            self.assertEqual([r[0] for r in db.conn().execute("SELECT inner FROM shots ORDER BY id")], [1, 0, 0, 1],
                             "10.2 and up on the air rifle face; never a miss")
            Core(db, now_ms=lambda: BASE).list_targets()


if __name__ == "__main__":
    unittest.main()
