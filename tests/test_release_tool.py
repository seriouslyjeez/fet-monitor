"""tools/release.py: the gh command it prints. GitHub's "Latest" must always be a FeT Monitor (server) release, so
the /releases/latest link in docs/INSTALL.md leads to the SD card image, never to FeT Bridge firmware."""
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools/release.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)


class LatestMark(unittest.TestCase):
    def command(self, kind):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sums = folder / "SHA256SUMS"
            sums.write_bytes(b"")
            built = lambda a: (folder, ["x.bin"], {"server": "v9.9.9", "firmware": "fw-9.9.9", "target": "tgt-9.9.9"}[kind], "T")
            out = io.StringIO()
            with mock.patch.object(rt, kind, built), mock.patch.object(rt, "sums_for", lambda f, n: sums), \
                    mock.patch.object(rt, "sign", lambda s, k: None), mock.patch.object(rt, "parse_sums", lambda b: {"x.bin": ""}), \
                    mock.patch.object(sys, "argv", ["release.py", kind, "9.9.9"]), contextlib.redirect_stdout(out):
                self.assertEqual(rt.main(), 0)
        return out.getvalue().split("Publish with:\n", 1)[1].split()

    def test_server_releases_are_latest(self):
        cmd = self.command("server")
        self.assertIn("--latest", cmd)
        self.assertNotIn("--latest=false", cmd)

    def test_firmware_releases_never_are(self):
        cmd = self.command("firmware")
        self.assertIn("--latest=false", cmd)
        self.assertNotIn("--latest", cmd)

    def test_target_firmware_releases_never_are(self):
        cmd = self.command("target")
        self.assertIn("--latest=false", cmd)
        self.assertIn("tgt-9.9.9", cmd)

    def test_target_kind_builds_a_checked_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist/target-firmware").mkdir(parents=True)
            prog = b"fet-target-mega/9.9.9"
            r = bytes([len(prog), 0, 0, 0]) + prog
            (root / "dist/target-firmware/fet-target-mega-9.9.9.hex").write_text(":" + (r + bytes([(-sum(r)) & 0xFF])).hex().upper() + "\n:00000001FF\n")
            (root / "dist/target-firmware/fet-target-mega-9.9.8.hex").write_text(":" + (r + bytes([(-sum(r)) & 0xFF])).hex().upper() + "\n:00000001FF\n")
            with mock.patch.object(rt, "ROOT", root):
                folder, names, tag, title = rt.target(mock.Mock(version="9.9.9"))
                self.assertEqual((names, tag, (folder / names[0]).is_file()), (["fet-target-mega-9.9.9.hex"], "tgt-9.9.9", True))
                with self.assertRaises(SystemExit, msg="the file says another version"):
                    rt.target(mock.Mock(version="9.9.8"))
                with self.assertRaises(SystemExit):
                    rt.target(mock.Mock(version="1.0.0"))


if __name__ == "__main__":
    unittest.main()
