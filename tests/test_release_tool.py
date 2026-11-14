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
            built = lambda a: (folder, ["x.bin"], {"server": "v9.9.9", "firmware": "fw-9.9.9", "target": "tgt-9.9.9", "esp32": "esp32-9.9.9", "display": "display-9.9.9"}[kind], "T")
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

    def test_esp32_firmware_releases_never_are(self):
        cmd = self.command("esp32")
        self.assertIn("--latest=false", cmd)
        self.assertIn("esp32-9.9.9", cmd)

    def test_display_releases_never_are(self):
        cmd = self.command("display")
        self.assertIn("--latest=false", cmd)
        self.assertIn("display-9.9.9", cmd)

    def test_display_kind_builds_a_checked_folder(self):
        def img(v, chip=b"\0\0", size=0x20000):
            head = bytes([0xE9, 3, 2, 0x20]) + bytes(8) + chip
            body = head + bytes(64) + b"MicroPython ... fet-display/" + v.encode() + b" ..."
            return body + b"\xff" * (size - len(body))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist/display").mkdir(parents=True)
            for name, data in (("9.9.9", img("9.9.9")), ("9.9.8", img("9.9.9")), ("9.9.7", img("9.9.7", chip=b"\x09\0")),
                               ("9.9.6", img("9.9.6", size=0x200000)), ("9.9.5", b"\xe9" + bytes(100))):
                (root / ("dist/display/fet-display-%s.bin" % name)).write_bytes(data)
            with mock.patch.object(rt, "ROOT", root):
                folder, names, tag, _ = rt.display(mock.Mock(version="9.9.9"))
                self.assertEqual((names, tag, (folder / names[0]).is_file()), (["fet-display-9.9.9.bin"], "display-9.9.9", True))
                for v, why in (("9.9.8", "not FeT Display 9.9.8"), ("9.9.7", "not an ESP32 image"), ("9.9.6", "larger than"), ("9.9.5", "not an ESP32")):
                    with self.assertRaisesRegex(rt.ReleaseError, why):
                        rt.display(mock.Mock(version=v))
                with self.assertRaises(SystemExit):
                    rt.display(mock.Mock(version="9.9.4"))

    def test_the_built_display_says_its_version(self):
        app = (ROOT / "firmware/display/lib/app.py").read_text()
        import re
        v = re.search(r'^VERSION = "([0-9.]+)"', app, re.M).group(1)
        self.assertIn('MARKER = "fet-display/%s"' % v, app, "the marker names the version (deepTENS and release.py look for it)")

    def test_esp32_kind_builds_a_checked_folder(self):
        import tempfile
        from tests.test_esp32fw import full_image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist/target-firmware").mkdir(parents=True)
            (root / "dist/target-firmware/fet-target-esp32-9.9.9.bin").write_bytes(full_image("9.9.9")[0])
            (root / "dist/target-firmware/fet-target-esp32-9.9.8.bin").write_bytes(full_image("9.9.9")[0])
            with mock.patch.object(rt, "ROOT", root):
                folder, names, tag, _ = rt.esp32(mock.Mock(version="9.9.9"))
                self.assertEqual((names, tag), (["fet-target-esp32-9.9.9.bin"], "esp32-9.9.9"))
                with self.assertRaises(SystemExit):
                    rt.esp32(mock.Mock(version="9.9.8"))

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
