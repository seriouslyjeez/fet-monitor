"""tools/accuracy/report.py: card placement fit and shot lists (numpy only; skipped without it)."""
import importlib.util
import math
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:                                   # pragma: no cover
    np = None


@unittest.skipIf(np is None, "numpy not installed")
class Report(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "report", Path(__file__).resolve().parent.parent / "tools/accuracy/report.py")
        cls.r = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.r)

    def test_shot_list(self):
        self.assertEqual(self.r.shot_list("55-58,60"), [55, 56, 57, 58, 60])
        self.assertEqual(self.r.shot_list("7"), [7])

    def test_fit_recovers_placement(self):
        A = np.array([[0, 0], [0, 8], [7, 4], [7, -4], [0, -8], [-7, -4]], float)
        t = math.radians(-12)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        B = A @ R.T + [3.0, -2.0]
        M, th = self.r.fit(A, B)
        self.assertAlmostEqual(th, -12.0, places=6)
        self.assertLess(np.abs(M - B).max(), 1e-9)
        M0, th0 = self.r.fit(A, B, rotate=False)
        self.assertEqual(th0, 0.0)
        self.assertGreater(np.abs(M0 - B).max(), 0.5)


if __name__ == "__main__":
    unittest.main()
