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
            built = lambda a: (folder, ["x.bin"], "v9.9.9" if kind == "server" else "fw-9.9.9", "T")
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


if __name__ == "__main__":
    unittest.main()
