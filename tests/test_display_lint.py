"""FeT Display's code read by pyflakes: a name used before it is set, or not defined at all, fails here instead of
on the display (2026-11-14: the target list's first drawing used a name set further down, "Display error: local
variable ..."; it only runs on a home display, so no other test reached it). Skipped without pyflakes
(pip install pyflakes)."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(importlib.util.find_spec("pyflakes"), "pyflakes not installed (pip install pyflakes)")
class Pyflakes(unittest.TestCase):
    def test_clean(self):
        files = [str(p) for p in sorted((ROOT / "firmware" / "display").rglob("*.py"))]
        self.assertGreater(len(files), 10)
        r = subprocess.run([sys.executable, "-m", "pyflakes", *files], capture_output=True, text=True)
        self.assertEqual(r.stdout + r.stderr, "", "pyflakes")


if __name__ == "__main__":
    unittest.main()
