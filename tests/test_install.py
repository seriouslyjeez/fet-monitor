"""install.sh in a fake root folder: stand-ins for systemctl/useradd/chown/runuser/id record every call, and the
systemctl stand-in really starts the INSTALLED server (from the fake /opt/fet-monitor) so the installer's own
health check talks to a real server. Needs bash 4+ (skipped otherwise). Not a substitute for a real Pi."""
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

from fetserver.core import Core
from fetserver.db import Database

ROOT = Path(__file__).resolve().parent.parent
SHOT = '{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":1.00, "y":1.00 , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'


def find_bash():
    for cand in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if cand and os.path.exists(cand):
            out = subprocess.run([cand, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout.strip()
            if out.isdigit() and int(out) >= 4:
                return cand
    return None


BASH = find_bash()

SYSTEMCTL = r'''#!/usr/bin/env python3
"""systemctl stand-in: remembers enabled/active units in $FETM_ROOT/.systemd.json; fet-monitor.service runs for real."""
import json, os, signal, subprocess, sys, time
root = os.environ["FETM_ROOT"]
state_path = os.path.join(root, ".systemd.json")
st = json.load(open(state_path)) if os.path.exists(state_path) else {"enabled": [], "active": {}}
with open(os.path.join(root, ".calls.log"), "a") as f:
    f.write("systemctl " + " ".join(sys.argv[1:]) + "\n")

def stop(u):
    pid = st["active"].pop(u, None)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                os.kill(pid, 0); time.sleep(0.1)
        except ProcessLookupError:
            pass

def start(u):
    if u in st["active"]:
        return
    pid = 0
    if u == "fet-monitor.service" or u == "fet-monitor":
        u = "fet-monitor.service"
        if not os.path.exists(os.path.join(root, "etc/systemd/system/fet-monitor.service")):
            sys.exit("Unit fet-monitor.service not found.")
        log = open(os.path.join(root, ".server.log"), "a")
        p = subprocess.Popen([sys.executable, "-m", "fetserver", "serve", "--data-dir", os.path.join(root, "var/lib/fet-monitor"),
                              "--host", "127.0.0.1", "--port", os.environ["FETM_PORT"], "--no-rulebook-download"],
                             cwd=os.path.join(root, "opt/fet-monitor"), stdout=log, stderr=log, start_new_session=True)
        pid = p.pid
    st["active"][u] = pid

def norm(u):
    return u if "." in u else u + ".service"

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = [a for a in sys.argv[1:] if a.startswith("--")]
cmd, units = args[0], [norm(u) for u in args[1:]]
rc = 0
if cmd == "daemon-reload":
    pass
elif cmd == "enable":
    st["enabled"] = sorted(set(st["enabled"]) | set(units))
    if "--now" in flags:
        for u in units: start(u)
elif cmd == "disable":
    st["enabled"] = sorted(set(st["enabled"]) - set(units))
    if "--now" in flags:
        for u in units: stop(u)
elif cmd == "start":
    for u in units: start(u)
elif cmd == "stop":
    for u in units: stop(u)
elif cmd == "restart":
    for u in units: stop(u); start(u)
elif cmd == "is-active":
    rc = 0 if all(u in st["active"] for u in units) else 3
json.dump(st, open(state_path, "w"))
sys.exit(rc)
'''

STUBS = {
    "systemctl": SYSTEMCTL,
    "id": textwrap.dedent('''\
        #!/bin/sh
        # id -u -> fake uid ($FAKE_UID, default 0); id -u NAME -> 1001 if useradd made it
        if [ "$#" -eq 1 ]; then echo "${FAKE_UID:-0}"; exit 0; fi
        grep -qx "$2" "$FETM_ROOT/.users" 2>/dev/null && { echo 1001; exit 0; }
        exit 1
        '''),
    "useradd": '#!/bin/sh\necho "useradd $*" >> "$FETM_ROOT/.calls.log"\nfor a; do last="$a"; done\necho "$last" >> "$FETM_ROOT/.users"\n',
    "userdel": '#!/bin/sh\necho "userdel $*" >> "$FETM_ROOT/.calls.log"\ngrep -vx "$1" "$FETM_ROOT/.users" > "$FETM_ROOT/.users.tmp" || true\nmv "$FETM_ROOT/.users.tmp" "$FETM_ROOT/.users"\n',
    "chown": '#!/bin/sh\necho "chown $*" >> "$FETM_ROOT/.calls.log"\n',
    "runuser": '#!/bin/sh\necho "runuser $*" >> "$FETM_ROOT/.calls.log"\nshift 3\nexec "$@"\n',
    "journalctl": '#!/bin/sh\ncat "$FETM_ROOT/.server.log" 2>/dev/null\n',
    "ip": '#!/bin/sh\necho "1.1.1.1 via 192.168.7.1 dev wlan0 src 192.168.7.42 uid 0"\n',
}

OS_RELEASE = {
    "bookworm": 'PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"\nID=raspbian\nID_LIKE=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n',
    "bookworm64": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n',
    "trixie": 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n',
    "bullseye": 'PRETTY_NAME="Raspbian GNU/Linux 11 (bullseye)"\nID=raspbian\nID_LIKE=debian\nVERSION_ID="11"\nVERSION_CODENAME=bullseye\n',
    "ubuntu": 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n',
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class InstallerBase(unittest.TestCase):
    """Fake root, stand-ins and helpers; no tests of its own (test_update_helper.py builds on it)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        (self.root / "etc").mkdir(parents=True)
        (self.root / "run/systemd/system").mkdir(parents=True)
        self.os("bookworm")
        self.port = free_port()
        self.addCleanup(self.stop_servers)

    def os(self, which):
        (self.root / "etc/os-release").write_text(OS_RELEASE[which])

    def stop_servers(self):
        state = self.root / ".systemd.json"
        if state.exists():
            for pid in json.loads(state.read_text())["active"].values():
                if pid:
                    try:
                        os.kill(pid, 15)
                        for _ in range(100):                   # wait until it has exited (and closed the database)
                            os.kill(pid, 0)
                            time.sleep(0.05)
                    except ProcessLookupError:
                        pass

    def run_install(self, *args, stdin="", uid=0):
        env = dict(os.environ, FETM_ROOT=str(self.root), FETM_PORT=str(self.port), FAKE_UID=str(uid),
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run([BASH, str(ROOT / "install.sh"), *args], input=stdin, capture_output=True, text=True,
                              env=env, timeout=180, start_new_session=True)   # no controlling tty: getpass reads stdin

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=3) as r:
            return json.load(r)

    def calls(self):
        p = self.root / ".calls.log"
        return p.read_text() if p.exists() else ""

    def mode(self, rel):
        return stat.S_IMODE((self.root / rel).stat().st_mode)

    def shoot(self, token, n):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/v1/events", method="POST",
                                     data=json.dumps({"device_id": "lane-1", "boot_count": 5, "uptime_ms": n * 1000, "events": [
                                         {"seq": n, "type": "shot", "uptime_ms": n * 1000, "raw": SHOT % n}]}).encode(),
                                     headers={"X-FET-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)



@unittest.skipUnless(BASH, "bash 4 or newer not found")
class Installer(InstallerBase):
    def test_fresh_install_then_rerun_upgrade(self):
        r = self.run_install(stdin="club range pw\nclub range pw\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tok = (self.root / "var/lib/fet-monitor/device_token").read_text().strip()
        self.assertGreaterEqual(len(tok), 20)
        self.assertIn(tok, r.stdout, "the token is printed")
        self.assertIn(f"http://192.168.7.42:{self.port}/", r.stdout)
        self.assertEqual(self.mode("var/lib/fet-monitor/device_token"), 0o600)
        self.assertEqual(self.mode("var/lib/fet-monitor/admin_password"), 0o600)
        self.assertEqual(self.mode("var/lib/fet-monitor"), 0o750)
        self.assertEqual(self.mode("var/backups/fet-monitor"), 0o750)
        for u in ("fet-monitor.service", "fet-monitor-backup.service", "fet-monitor-backup.timer", "fet-monitor-update.path", "fet-monitor-update.service"):
            self.assertTrue((self.root / "etc/systemd/system" / u).exists(), u)
        state = json.loads((self.root / ".systemd.json").read_text())
        self.assertEqual(state["enabled"], ["fet-monitor-backup.timer", "fet-monitor-update.path", "fet-monitor.service", "fet-setup.service", "fet-system.path"])
        self.assertEqual(self.mode("var/lib/fet-monitor/system"), 0o750)
        self.assertTrue((self.root / "etc/systemd/system/fet-setup.service").exists())
        self.assertTrue((self.root / "opt/fet-monitor/fetsetup/portal.py").exists() and (self.root / "opt/fet-monitor/fetsetup/web/setup.html").exists())
        self.assertEqual(json.loads((self.root / "var/lib/fet-setup/setup.done").read_text()), {"by": "install.sh"},
                         "installed over a working network: no setup network on the next start")
        self.assertEqual(self.mode("opt/fet-monitor/bin/fet-monitor-update"), 0o755)
        self.assertEqual(self.mode("var/lib/fet-monitor/update"), 0o750)
        self.assertTrue(self.get("/api/v1/health")["ok"])
        self.assertIn("useradd --system --no-create-home --home-dir /var/lib/fet-monitor --shell /usr/sbin/nologin fet-monitor", self.calls())
        self.assertIn("runuser -u fet-monitor -- python3 -m fetserver set-password", self.calls())
        self.assertTrue((self.root / "opt/fet-monitor/NOTICE").exists())
        # a shot arrives; then re-run (upgrade): token, password and data kept, code replaced, no password prompt
        self.shoot(tok, 1)
        self.assertEqual(self.get("/api/v1/targets")["targets"][0]["shot_count"], 1)
        pw_before = (self.root / "var/lib/fet-monitor/admin_password").read_text()
        (self.root / "opt/fet-monitor/fetserver/stale_module.py").write_text("x = 1\n")
        r = self.run_install(stdin="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already set (kept)", r.stdout)
        self.assertIn("kept the existing token", r.stdout)
        self.assertEqual((self.root / "var/lib/fet-monitor/device_token").read_text().strip(), tok)
        self.assertEqual((self.root / "var/lib/fet-monitor/admin_password").read_text(), pw_before)
        self.assertFalse((self.root / "opt/fet-monitor/fetserver/stale_module.py").exists(), "code replaced, not merged")
        self.assertEqual(sorted(p.name for p in (self.root / "opt/fet-monitor").iterdir()), ["LICENSE", "NOTICE", "bin", "fetserver", "fetsetup"],
                         "no fetserver.old / fetserver.new left behind")
        self.assertIn("stopping the running server for the upgrade", r.stdout)
        self.assertEqual(self.get("/api/v1/targets")["targets"][0]["shot_count"], 1, "data untouched")
        self.assertEqual(self.calls().count("useradd"), 1, "user created once")
        # --reset-password asks again
        r = self.run_install("--reset-password", stdin="another range pw\nanother range pw\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual((self.root / "var/lib/fet-monitor/admin_password").read_text(), pw_before)

    def test_image_build(self):
        """--image (pi-gen, in a chroot): no systemd running, no password, nothing started, no setup-done mark."""
        import shutil
        shutil.rmtree(self.root / "run/systemd/system")
        r = self.run_install("--image")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("The first start opens the setup network", r.stdout)
        self.assertFalse((self.root / "var/lib/fet-setup/setup.done").exists())
        self.assertFalse((self.root / "var/lib/fet-monitor/admin_password").exists())
        state = json.loads((self.root / ".systemd.json").read_text())
        self.assertIn("fet-setup.service", state["enabled"])
        self.assertEqual(state["active"], {}, "nothing started in the image")
        self.assertNotIn("daemon-reload", self.calls())
        self.assertFalse((self.root / "var/lib/fet-monitor/device_token").exists(), "no token in the image: every Pi makes its own")
        self.assertIn("fet-system.path", state["enabled"])

    def test_no_network_now_means_setup_on_next_start(self):
        (self.bin / "ip").write_text("#!/bin/sh\nexit 2\n")
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the next start opens the setup network", r.stdout)
        self.assertFalse((self.root / "var/lib/fet-setup/setup.done").exists())

    def test_installer_never_opens_the_database(self):
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped (--no-password)", r.stdout)
        self.assertFalse((self.root / "var/lib/fet-monitor/admin_password").exists())
        self.stop_servers()                                       # with no server running, a re-run must not create one
        (self.root / ".systemd.json").write_text(json.dumps({"enabled": [], "active": {}}))
        db = self.root / "var/lib/fet-monitor/fetserver.db"
        before = db.stat().st_mtime_ns
        bin_systemctl = self.bin / "systemctl"
        bin_systemctl.write_text('#!/bin/sh\necho "systemctl $*" >> "$FETM_ROOT/.calls.log"\n[ "$1" = "is-active" ] && exit 3\nexit 0\n')
        r = self.run_install("--no-password")
        self.assertNotEqual(r.returncode, 0, "no server answers, so the installer must fail loudly")
        self.assertIn("did not answer", r.stderr)
        self.assertEqual(db.stat().st_mtime_ns, before)

    def test_migrates_an_old_freetarget_install(self):
        old = self.root / "var/lib/freetarget"
        old.mkdir(parents=True)
        (old / "device_token").write_text("old-token-123456789\n")
        core = Core(Database(str(old / "fetserver.db")))
        core.handle_events({"device_id": "lane-9", "boot_count": 3, "uptime_ms": 5000, "events": [
            {"seq": 1, "type": "shot", "uptime_ms": 4000, "raw": SHOT % 1}]})
        del core
        (self.root / "opt/freetarget-server/fetserver").mkdir(parents=True)
        (self.root / "etc/systemd/system").mkdir(parents=True)
        (self.root / "etc/systemd/system/freetarget-server.service").write_text("[Unit]\n")
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Found an older install", r.stdout)
        backups = list((self.root / "var/backups").glob("freetarget-full-before-install-*.tar.gz"))
        self.assertEqual(len(backups), 1)
        listing = subprocess.run(["tar", "-tzf", str(backups[0])], capture_output=True, text=True).stdout
        self.assertIn("var/lib/freetarget/fetserver.db", listing)
        self.assertIn("etc/systemd/system/freetarget-server.service", listing)
        self.assertEqual((self.root / "var/lib/fet-monitor/device_token").read_text().strip(), "old-token-123456789")
        self.assertIn("old-token-123456789", r.stdout)
        t = self.get("/api/v1/targets")["targets"]
        self.assertEqual([(x["device_id"], x["shot_count"]) for x in t], [("lane-9", 1)], "the old shots came along")
        self.assertTrue(old.exists(), "the old install is kept for rollback")
        self.assertIn("systemctl disable freetarget-server", self.calls())
        # a re-run does not migrate again
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Found an older install", r.stdout)
        self.assertIn("already migrated", r.stdout)

    def test_preflight(self):
        r = self.run_install(uid=1000)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("run with sudo", r.stderr)
        for which, msg in (("bullseye", "too old"), ("ubuntu", "not Raspberry Pi OS")):
            self.os(which)
            r = self.run_install("--no-password")
            self.assertNotEqual(r.returncode, 0, which)
            self.assertIn(msg, r.stderr, which)
        self.os("bookworm")
        shutil.rmtree(self.root / "run/systemd/system")
        r = self.run_install("--no-password")
        self.assertIn("systemd is not running", r.stderr)
        self.assertFalse((self.root / "opt").exists(), "nothing installed after a failed check")

    def test_trixie_warns_but_installs(self):
        self.os("trixie")
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("has not been tested", r.stderr)

    def test_64bit_bookworm(self):
        self.os("bookworm64")
        r = self.run_install("--no-password")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_uninstall_keeps_data_purge_removes_it(self):
        self.assertEqual(self.run_install("--no-password").returncode, 0)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.root / "opt/fet-monitor").exists())
        self.assertFalse((self.root / "etc/systemd/system/fet-monitor.service").exists())
        self.assertTrue((self.root / "var/lib/fet-monitor/device_token").exists())
        self.assertTrue((self.root / "var/lib/fet-monitor/fetserver.db").exists())
        r = self.run_install("--uninstall", "--purge", stdin="no\n")
        self.assertIn("Not confirmed", r.stdout)
        self.assertTrue((self.root / "var/lib/fet-monitor").exists())
        r = self.run_install("--uninstall", "--purge", stdin="DELETE\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.root / "var/lib/fet-monitor").exists())
        self.assertFalse((self.root / "var/backups/fet-monitor").exists())
        self.assertIn("userdel fet-monitor", self.calls())

    def test_options(self):
        r = self.run_install("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--reset-password", r.stdout)
        for bad in (["--bogus"], ["--purge"], ["--reset-password", "--no-password"]):
            r = self.run_install(*bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertFalse((self.root / "opt").exists())

    def test_release_archive_installs(self):
        out = Path(self.tmp.name) / "dist"
        r = subprocess.run([sys.executable, str(ROOT / "tools/make_release.py"), "--dirty", "--out", str(out)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        arc = next(out.glob("fet-monitor-*.tar.gz"))
        sums = (out / "SHA256SUMS").read_text()
        self.assertEqual(sums.split()[0], hashlib.sha256(arc.read_bytes()).hexdigest())
        with tarfile.open(arc) as t:
            names = t.getnames()
            top = names[0].split("/")[0]
            for need in ("install.sh", "fetserver/core.py", "deploy/fet-monitor.service", "deploy/migrate-from-freetarget.sh",
                         "LICENSE", "NOTICE", "docs/INSTALL.md"):
                self.assertIn(f"{top}/{need}", names)
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc") or n.split("/")[1:2] in (["tests"], ["firmware"], ["dist"])])
            self.assertEqual(t.getmember(f"{top}/install.sh").mode, 0o755)
            self.assertTrue(all(m.uid == 0 and m.gid == 0 for m in t.getmembers()))
            unpack = Path(self.tmp.name) / "unpack"
            t.extractall(unpack)
        again = Path(self.tmp.name) / "dist2"
        subprocess.run([sys.executable, str(ROOT / "tools/make_release.py"), "--dirty", "--out", str(again)], capture_output=True, timeout=120)
        self.assertEqual((again / arc.name).read_bytes(), arc.read_bytes(), "reproducible: same input, same bytes")
        env = dict(os.environ, FETM_ROOT=str(self.root), FETM_PORT=str(self.port), FAKE_UID="0",
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        r = subprocess.run([BASH, str(unpack / top / "install.sh"), "--no-password"], capture_output=True, text=True,
                           env=env, timeout=180, start_new_session=True, cwd=str(Path(self.tmp.name)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.get("/api/v1/health")["ok"])


if __name__ == "__main__":
    unittest.main()
