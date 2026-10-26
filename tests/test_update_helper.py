"""The root update helper (deploy/fet-monitor-update.sh) in the installer's fake root: a signed release is checked again,
the database is backed up, the release's install.sh runs, and a release that does not start is rolled back - code AND
database (the broken release below upgrades the database structure before it crashes)."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import unittest
import urllib.request
from pathlib import Path

from tests.test_install import BASH, InstallerBase
from tests.test_releases import make_key, sign

ROOT = Path(__file__).resolve().parent.parent
PARTS = ["install.sh", "LICENSE", "NOTICE", "README.md", "docs/INSTALL.md", "fetserver", "deploy"]


@unittest.skipUnless(BASH and shutil.which("ssh-keygen"), "bash 4+ and ssh-keygen needed")
class UpdateHelper(InstallerBase):
    def setUp(self):
        super().setUp()
        self.keys = Path(self.tmp.name) / "keys"
        self.keys.mkdir()
        self.key, signers = make_key(self.keys)
        self.other, _ = make_key(self.keys, "other")
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        shutil.copyfile(signers, self.root / "opt/fet-monitor/fetserver/release_signers")   # trust the test key
        self.token = (self.root / "var/lib/fet-monitor/device_token").read_text().strip()
        self.shoot(self.token, 1)
        self.old_version = self.health()["version"]

    def health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/v1/health", timeout=3) as r:
            return json.load(r)

    def shots(self):
        return self.get("/api/v1/targets")["targets"][0]["shot_count"]

    def build(self, version, broken=False, extra=None):
        work = Path(self.tmp.name) / f"rel-{version}{'-' + extra if extra else ''}"
        top = work / f"fet-monitor-{version}"
        for part in PARTS:
            src = ROOT / part
            if src.is_dir():
                shutil.copytree(src, top / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                (top / part).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, top / part)
        if extra:                                     # same version, different contents
            (top / "fetserver/extra.py").write_text(f"# {extra}\n")
        init = top / "fetserver/__init__.py"
        init.write_text(init.read_text().replace(self.old_version, version))
        if broken:      # upgrades the database structure, then refuses to serve
            with open(top / "fetserver/db.py", "a") as f:
                f.write('\nMIGRATIONS.append("CREATE TABLE future_thing (x INTEGER);")\n')
            main = top / "fetserver/__main__.py"
            main.write_text(main.read_text().replace("def cmd_serve(args, db: Database, data_dir: Path) -> int:\n",
                                                     "def cmd_serve(args, db: Database, data_dir: Path) -> int:\n    raise SystemExit('broken release')\n"))
        name = f"fet-monitor-{version}.tar.gz"
        with tarfile.open(work / name, "w:gz") as t:
            t.add(top, arcname=top.name)
        return work / name

    def stage(self, version, archive, key=None, request=None):
        st = self.root / "var/lib/fet-monitor/update/staging"
        st.mkdir(parents=True, exist_ok=True)
        data = archive.read_bytes()
        sums = f"{hashlib.sha256(data).hexdigest()}  {archive.name}\n".encode()
        (st / archive.name).write_bytes(data)
        (st / "SHA256SUMS").write_bytes(sums)
        (st / "SHA256SUMS.sig").write_bytes(sign(key or self.key, sums, self.keys))
        (self.root / "var/lib/fet-monitor/update/request.json").write_text(request or json.dumps({"version": version}))

    def run_helper(self):
        env = dict(os.environ, FETM_ROOT=str(self.root), FETM_PORT=str(self.port), FETM_HEALTH_WAIT="6",
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        r = subprocess.run([BASH, str(self.root / "opt/fet-monitor/bin/fet-monitor-update")], capture_output=True, text=True,
                           env=env, timeout=300, start_new_session=True)
        st = json.loads((self.root / "var/lib/fet-monitor/update/status.json").read_text())
        return r, st

    def test_good_update(self):
        self.stage("9.9.1", self.build("9.9.1"))
        r, st = self.run_helper()
        self.assertEqual((r.returncode, st["state"]), (0, "done"), r.stderr + st["message"])
        self.assertEqual(self.health()["version"], "9.9.1")
        self.assertEqual(self.shots(), 1, "data kept")
        self.assertEqual(len(list((self.root / "var/backups/fet-monitor/pre-update").glob("fet-*.db"))), 1)
        self.assertFalse((self.root / "var/lib/fet-monitor/update/request.json").exists())
        self.assertTrue((self.root / "opt/fet-monitor/fetserver.prev").is_dir(), "the old code is kept for a manual rollback")

    def test_broken_release_is_rolled_back_with_its_database(self):
        self.stage("9.9.2", self.build("9.9.2", broken=True))
        r, st = self.run_helper()
        self.assertEqual((r.returncode, st["state"]), (1, "rolled_back"), st["message"])
        self.assertEqual(self.health()["version"], self.old_version, "the previous version is running again")
        self.assertEqual(self.shots(), 1)
        out = subprocess.run(["python3", "-c", "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA user_version').fetchone()[0])",
                              str(self.root / "var/lib/fet-monitor/fetserver.db")], capture_output=True, text=True).stdout.strip()
        from fetserver.db import MIGRATIONS
        self.assertEqual(int(out), len(MIGRATIONS), "the database is back at the old structure")

    def test_bad_signature_changes_nothing(self):
        self.stage("9.9.3", self.build("9.9.3"), key=self.other)
        r, st = self.run_helper()
        self.assertEqual((r.returncode, st["state"]), (1, "failed"))
        self.assertIn("signature is NOT valid", st["message"])
        self.assertEqual(self.health()["version"], self.old_version)
        self.assertFalse((self.root / "var/backups/fet-monitor/pre-update").exists(), "not even a backup was needed")

    def test_staged_archive_swapped_after_the_server_checked_it(self):
        archive = self.build("9.9.4")
        self.stage("9.9.4", archive)
        evil = self.build("9.9.4", extra="swapped")     # a well-formed 9.9.4, just not the one that was signed
        shutil.copyfile(evil, self.root / f"var/lib/fet-monitor/update/staging/{archive.name}")
        r, st = self.run_helper()
        self.assertEqual(st["state"], "failed")
        self.assertIn("did not pass its checks", st["message"])
        self.assertEqual(self.health()["version"], self.old_version)

    def test_untrusted_request_contents(self):
        archive = self.build("9.9.6")
        for req, why in ((json.dumps({"version": "../../etc"}), "bad update request"), ("not json", "bad update request"),
                         (json.dumps({"version": "9.9.7"}), "missing or not a plain file")):
            self.stage("9.9.6", archive, request=req)
            r, st = self.run_helper()
            self.assertEqual(st["state"], "failed", req)
            self.assertIn(why, st["message"], req)
        st_dir = self.root / "var/lib/fet-monitor/update/staging"
        (st_dir / "SHA256SUMS").unlink()
        os.symlink("/etc/hosts", st_dir / "SHA256SUMS")
        (self.root / "var/lib/fet-monitor/update/request.json").write_text(json.dumps({"version": "9.9.6"}))
        r, st = self.run_helper()
        self.assertIn("not a plain file", st["message"], "a symlink is refused")
        self.assertEqual(self.health()["version"], self.old_version)

    def test_no_request_does_nothing(self):
        env = dict(os.environ, FETM_ROOT=str(self.root), PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        r = subprocess.run([BASH, str(self.root / "opt/fet-monitor/bin/fet-monitor-update")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.root / "var/lib/fet-monitor/update/status.json").exists())


if __name__ == "__main__":
    unittest.main()
