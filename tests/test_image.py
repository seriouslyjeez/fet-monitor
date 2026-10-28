"""The SD card image: the pi-gen stage, the GitHub Actions workflow and the image signing in tools/release.py. The
image build itself runs on GitHub (pi-gen needs Linux and Docker); these tests check what can be checked here."""
import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "image/stage-fet"
WORKFLOW = (ROOT / ".github/workflows/image.yml").read_text()


class Stage(unittest.TestCase):
    def test_scripts(self):
        for rel in ("prerun.sh", "00-fet/01-run.sh", "00-fet/02-run-chroot.sh"):
            p = STAGE / rel
            self.assertTrue(os.access(p, os.X_OK), f"{rel} must be executable (pi-gen skips it otherwise)")
            self.assertEqual(subprocess.run(["bash", "-n", str(p)]).returncode, 0, rel)
        chroot = (STAGE / "00-fet/02-run-chroot.sh").read_text()
        self.assertIn("./install.sh --image", chroot)
        self.assertIn("systemctl disable ssh", chroot)
        self.assertIn("copy_previous", (STAGE / "prerun.sh").read_text())
        self.assertTrue((STAGE / "EXPORT_IMAGE").exists(), "the stage is exported as the image")
        pkgs = (STAGE / "00-fet/00-packages").read_text().split()
        for p in ("python3", "network-manager", "dnsmasq-base", "rfkill", "openssh-client"):
            self.assertIn(p, pkgs)


class Workflow(unittest.TestCase):
    def test_built_from_the_signed_release_only(self):
        verify = WORKFLOW.index("ssh-keygen -Y verify -f ../fetserver/release_signers")
        self.assertLess(verify, WORKFLOW.index("usimd/pi-gen-action"), "the signature is checked before anything is built")
        self.assertLess(WORKFLOW.index("sha256sum -c"), WORKFLOW.index("tar -xzf"))
        self.assertIn("-n fet-monitor-release", WORKFLOW)
        self.assertIn("startsWith(github.event.release.tag_name, 'v')", WORKFLOW, "server releases only (not fw-...)")

    def test_image_settings(self):
        for line in ("stage-list: stage0 stage1 stage2 ./image/stage-fet", "enable-ssh: 0", 'password: ""', "enable-cloud-init: 0",
                     "disable-first-boot-user-rename: 1", "pi-gen-version: arm64", "release: trixie", "compression: xz"):
            self.assertIn(line, WORKFLOW)
        self.assertIn("IMAGE-SHA256SUMS", WORKFLOW)


def release_tool():
    spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools/release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ImageSigning(unittest.TestCase):
    def setUp(self):
        self.rel = release_tool()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rel.ROOT = Path(tmp.name)
        self.signed = []

    def run_image(self, listed_hash=None, extra_name=None):
        name = "fet-monitor-0.6.30-pi.img.xz"
        data = b"an image"

        def fake_run(cmd, check=False, **kw):
            if cmd[:3] == ["gh", "release", "download"]:
                d = Path(cmd[cmd.index("-D") + 1])
                (d / name).write_bytes(data)
                h = listed_hash or hashlib.sha256(data).hexdigest()
                (d / "IMAGE-SHA256SUMS").write_text(f"{h}  {name}\n" + (f"{'0' * 64}  {extra_name}\n" if extra_name else ""))
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(self.rel.subprocess, "run", fake_run), mock.patch.object(self.rel, "sign", lambda sums, key: self.signed.append(sums.name)):
            return self.rel.image(mock.Mock(version="0.6.30", key="k", publish=False))

    def test_checked_here_then_signed(self):
        self.assertEqual(self.run_image(), 0)
        self.assertEqual(self.signed, ["IMAGE-SHA256SUMS"])

    def test_a_different_image_is_not_signed(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_image(listed_hash="1" * 64)
        self.assertIn("does not match", str(cm.exception))
        with self.assertRaises(SystemExit):
            self.run_image(extra_name="other.img.xz")
        self.assertEqual(self.signed, [])
        with self.assertRaises(SystemExit):
            self.rel.image(mock.Mock(version="latest", key="k", publish=False))


if __name__ == "__main__":
    unittest.main()
