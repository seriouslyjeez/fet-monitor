"""Range Control password and sessions."""
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fetserver import admin
from fetserver.admin import ADMIN_IDLE_S, ADMIN_MAX_S, FAIL_LIMIT, LOCK_S, Admin, hash_password, verify_password, write_password

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Hashing(unittest.TestCase):
    def test_round_trip_both_algorithms(self):
        algs = ["pbkdf2-sha256"] + (["scrypt"] if hasattr(hashlib, "scrypt") else [])
        for alg in algs:
            rec = hash_password("correct horse", alg=alg)
            self.assertEqual(rec["alg"], alg)
            self.assertTrue(verify_password("correct horse", rec))
            self.assertFalse(verify_password("correct horsE", rec))
            self.assertFalse(verify_password("", rec))
        self.assertNotEqual(hash_password("x" * 8)["hash"], hash_password("x" * 8)["hash"], "salted")

    def test_garbage_records_never_verify(self):
        for rec in (None, {}, {"alg": "md5", "salt": "00", "hash": "00"}, {"alg": "pbkdf2-sha256", "salt": "zz", "iter": 1, "hash": ""},
                    {"alg": "pbkdf2-sha256", "salt": "00"}, "text"):
            self.assertFalse(verify_password("anything", rec))

    def test_file_is_private_and_short_passwords_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                write_password(Path(d), "short")
            self.assertEqual(os.listdir(d), [], "nothing written for a refused password")
            p = write_password(Path(d), "long enough")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(sorted(os.listdir(d)), ["admin_password"])


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.clock = Clock()
        self.a = Admin(self.dir, now=self.clock)

    def test_unset_password_refuses_everything(self):
        self.assertFalse(self.a.password_set())
        self.assertEqual(self.a.login("whatever1", "1.2.3.4"), ("unset", None))
        self.assertFalse(self.a.check("x"))
        self.assertFalse(Admin(None).password_set())

    def test_login_check_logout(self):
        write_password(self.dir, "range-pass")
        self.assertEqual(self.a.login("wrong-pass", "ip1"), ("bad", None))
        st, tok = self.a.login("range-pass", "ip1")
        self.assertEqual(st, "ok")
        self.assertTrue(self.a.check(tok))
        self.assertFalse(self.a.check(tok + "x"))
        self.assertFalse(self.a.check(None))
        self.a.logout(tok)
        self.assertFalse(self.a.check(tok))

    def test_idle_and_absolute_expiry(self):
        write_password(self.dir, "range-pass")
        _, tok = self.a.login("range-pass", "ip1")
        self.clock.t += ADMIN_IDLE_S - 1
        self.assertTrue(self.a.check(tok), "use renews the idle timer")
        while self.clock.t < 1000 + ADMIN_MAX_S - ADMIN_IDLE_S:
            self.clock.t += ADMIN_IDLE_S - 1
            self.assertTrue(self.a.check(tok))
        self.clock.t = 1000 + ADMIN_MAX_S + 1
        self.assertFalse(self.a.check(tok), "never longer than the absolute limit")
        _, tok = self.a.login("range-pass", "ip1")
        self.clock.t += ADMIN_IDLE_S + 1
        self.assertFalse(self.a.check(tok), "idle too long")

    def test_changing_the_password_ends_sessions(self):
        write_password(self.dir, "range-pass")
        _, tok = self.a.login("range-pass", "ip1")
        write_password(self.dir, "new-range-pass")
        self.assertFalse(self.a.check(tok))
        self.assertEqual(self.a.login("range-pass", "ip1")[0], "bad")
        self.assertEqual(self.a.login("new-range-pass", "ip1")[0], "ok")

    def test_lockout_per_address(self):
        write_password(self.dir, "range-pass")
        for _ in range(FAIL_LIMIT):
            self.assertEqual(self.a.login("nope-nope", "ip1")[0], "bad")
        st, wait = self.a.login("range-pass", "ip1")
        self.assertEqual(st, "locked", "even the right password while locked")
        self.assertGreater(wait, 0)
        self.assertEqual(self.a.login("range-pass", "ip2")[0], "ok", "other addresses are not affected")
        self.clock.t += LOCK_S + 1
        self.assertEqual(self.a.login("range-pass", "ip1")[0], "ok")

    def test_failures_spread_out_do_not_lock(self):
        write_password(self.dir, "range-pass")
        for _ in range(FAIL_LIMIT * 3):
            self.a.login("nope-nope", "ip1")
            self.clock.t += admin.FAIL_WINDOW_S / (FAIL_LIMIT - 1) + 1
        self.assertEqual(self.a.login("range-pass", "ip1")[0], "ok")

    def test_non_text_password(self):
        write_password(self.dir, "range-pass")
        for bad in (None, 123, ["range-pass"]):
            self.assertEqual(self.a.login(bad, "ip1")[0], "bad")


class Cli(unittest.TestCase):
    def run_cli(self, d, stdin):
        return subprocess.run([sys.executable, "-m", "fetserver", "set-password", "--data-dir", d, "--password-stdin"],
                              input=stdin, capture_output=True, text=True, cwd=ROOT, timeout=60)

    def test_set_password_never_opens_the_database(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli(d, "short\n")
            self.assertEqual(r.returncode, 1)
            r = self.run_cli(d, "a fine password\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(os.listdir(d)), ["admin_password"])
            self.assertEqual(Admin(Path(d)).login("a fine password", "x")[0], "ok")


if __name__ == "__main__":
    unittest.main()
