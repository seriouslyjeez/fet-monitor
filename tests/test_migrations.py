"""Every database a released (or deployed) server ever created must upgrade cleanly to the current schema.
A migration that has run anywhere must never be edited: add a new one (v9 was once edited in place and broke a Pi)."""
import os
import sqlite3
import tempfile
import unittest

from fetserver.core import Core
from fetserver.db import MIGRATIONS, Database, _split

BASE = 1_000_000_000


def build_at(path, version):
    """A database exactly as a server with `version` migrations left it."""
    conn = sqlite3.connect(path)
    for i in range(version):
        for stmt in _split(MIGRATIONS[i]):
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version={i + 1}")
    conn.commit()
    return conn


class Migrations(unittest.TestCase):
    def test_every_old_version_upgrades(self):
        for v in range(0, len(MIGRATIONS)):
            with self.subTest(from_version=v), tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "t.db")
                build_at(path, v).close()
                db = Database(path)
                self.assertEqual(db.conn().execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
                self.assertEqual(db.integrity_check(), "ok")
                core = Core(db, now_ms=lambda: BASE)
                core.handle_heartbeat({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 0})
                core.list_targets()
                core.matches.tick()

    def test_a_pi_with_v9_and_a_running_match(self):
        """The case that broke: v9 (no 'paced' column) with a match in progress, then the new server starts."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            conn = build_at(path, 9)
            conn.execute("INSERT INTO targets (device_id, first_seen_ms, last_seen_ms) VALUES ('lane-1', ?, ?)", (BASE, BASE))
            fmt = '{"id":"x","name":"X","scoring":"decimal","phases":[{"kind":"sight","label":"S","seconds":60,"shots":0,' \
                  '"position":"","warnings":[],"start":"","end":"","rule":""},{"kind":"record","label":"R","seconds":60,"shots":1,' \
                  '"position":"","warnings":[],"start":"","end":"","rule":""}]}'
            mid = conn.execute("INSERT INTO matches (name, format_json, state, created_ms, started_ms, phase, phase_started_ms) "
                               "VALUES ('Club', ?, 'running', ?, ?, 0, ?)", (fmt, BASE, BASE, BASE)).lastrowid
            conn.execute("INSERT INTO match_lanes (match_id, device_id, lane) VALUES (?, 'lane-1', 1)", (mid,))
            conn.execute("INSERT INTO match_log (match_id, at_ms, kind, phase) VALUES (?, ?, 'phase_start', 0)", (mid, BASE))
            conn.commit()
            conn.close()
            now = [BASE + 61_000]
            core = Core(Database(path), now_ms=lambda: now[0])
            self.assertEqual(core.matches.tick(), 1, "the clock moves on past the sighting phase")
            t = core.list_targets()[0]
            self.assertEqual((t["match"]["label"], t["match"]["paced"]), ("R", "auto"))
            self.assertEqual(core.matches.detail(mid)["state"], "running")

    def test_migrations_that_ran_somewhere_are_unchanged(self):
        """Fingerprints of every migration that has left this machine (v1-v8 released, v9-v10 ran on a Pi, v11-v15 reported). If this fails,
        do not edit the old migration: put it back and add a new one."""
        import hashlib
        for i, fp in enumerate(FROZEN):
            self.assertEqual(hashlib.sha256(MIGRATIONS[i].encode()).hexdigest()[:16], fp, f"migration v{i + 1} was changed")


FROZEN = ['58bb67ed1ccce334', '24a5d89bea995aa9', 'a18f52b1c4efba6b', '5d4bb11e4baeb999', 'e44a05e58bd4b4f8', '446e1e8368f51072', 'cd825dfc2285a344', 'c1eddbfe1f2e9dba', 'd4601e483a6c5bff', '65323a43f0cdc3aa', 'b46244a5306832f2', '695784f7e931f966', 'ddf8be689bfcbb72', '5139bdbbdf7024a2', '5b4b72f1b211f552']


if __name__ == "__main__":
    unittest.main()


class FailSoft(unittest.TestCase):
    def test_a_match_control_error_never_breaks_the_target_list(self):
        with tempfile.TemporaryDirectory() as d:
            core = Core(Database(os.path.join(d, "t.db")), now_ms=lambda: BASE)
            core.handle_heartbeat({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 0})

            def boom(*a):
                raise KeyError("paced")
            core.matches.target_info = boom
            with self.assertLogs("fetserver.core", "ERROR"):
                t = core.list_targets()
            self.assertEqual((t[0]["device_id"], t[0]["match"]), ("lane-1", None))
