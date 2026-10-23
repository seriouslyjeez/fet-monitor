import math
import random
import unittest
from decimal import Decimal

from fetserver.scoring import MISS, Score, ring_outer_radius_h, ring_reach_h, score_xy, to_hundredths


class ToHundredths(unittest.TestCase):
    def test_values(self):
        self.assertEqual(to_hundredths(Decimal("-7.07")), -707)
        self.assertEqual(to_hundredths(-7.07), -707)
        self.assertEqual(to_hundredths("21.21"), 2121)
        self.assertEqual(to_hundredths(0), 0)

    def test_rounds_half_away_from_zero(self):
        self.assertEqual(to_hundredths(Decimal("2.675")), 268)
        self.assertEqual(to_hundredths(Decimal("-2.675")), -268)
        self.assertEqual(to_hundredths(Decimal("0.004")), 0)


class Geometry(unittest.TestCase):
    def test_ring_diameters_match_issf(self):
        # outside diameters in mm: ten 0.5 ... ring 1 45.5
        expected = {10: 0.5, 9: 5.5, 8: 10.5, 7: 15.5, 6: 20.5, 5: 25.5, 4: 30.5, 3: 35.5, 2: 40.5, 1: 45.5}
        for ring, dia in expected.items():
            self.assertEqual(ring_outer_radius_h(ring) * 2, round(dia * 100))

    def test_reach_every_2_5_mm(self):
        self.assertEqual([ring_reach_h(k) for k in range(10, 0, -1)], [250 * i for i in range(1, 11)])


class Vectors(unittest.TestCase):
    """The table in section 7 of the spec, plus the two real shots from the target."""
    CASES = [
        (0, 0, 10, 109), (25, 0, 10, 109), (26, 0, 10, 108), (250, 0, 10, 100), (251, 0, 9, 99),
        (500, 0, 9, 90), (-707, 2121, 2, 20), (2500, 0, 1, 10), (2501, 0, 0, 0),
        (-361, -1456, 4, 49),      # live shot: r = 15.0009 mm, just outside the ring-5 line
        (1500, 0, 5, 50),          # exactly on the line scores the higher ring
    ]

    def test_vectors(self):
        for x, y, ring, tenths in self.CASES:
            with self.subTest(x=x, y=y):
                self.assertEqual(score_xy(x, y), Score(ring, tenths))

    def test_miss_constant(self):
        self.assertEqual(MISS, Score(0, 0))


class Properties(unittest.TestCase):
    def test_every_ring_line_is_inclusive(self):
        for ring in range(1, 11):
            reach = ring_reach_h(ring)
            self.assertEqual(score_xy(reach, 0).ring, ring)
            self.assertEqual(score_xy(reach + 1, 0).ring, ring - 1)

    def test_monotonic_and_consistent_along_axis(self):
        prev = 10_000
        for r in range(0, 2700):
            sc = score_xy(r, 0)
            self.assertLessEqual(sc.tenths, prev)
            prev = sc.tenths
            self.assertEqual(sc.tenths // 10, sc.ring)
            self.assertTrue(0 <= sc.tenths <= 109)

    def test_symmetry(self):
        rng = random.Random(7)
        for _ in range(500):
            x, y = rng.randint(-2600, 2600), rng.randint(-2600, 2600)
            s = score_xy(x, y)
            for variant in ((-x, y), (x, -y), (y, x), (-y, -x)):
                self.assertEqual(score_xy(*variant), s)

    def test_agrees_with_float_reference_away_from_lines(self):
        rng = random.Random(11)
        checked = 0
        while checked < 3000:
            x, y = rng.randint(-2700, 2700), rng.randint(-2700, 2700)
            r = math.hypot(x, y)
            # skip points within 0.02 mm of any sub-band line, where float and exact may legitimately differ
            if abs((r / 25.0) - round(r / 25.0)) * 25 < 2:
                continue
            n = math.ceil(r / 25.0) - 1
            expected = Score(0, 0) if n >= 100 else Score(10 - max(0, math.ceil((n + 1 - 10) / 10)), 109 - n)
            self.assertEqual(score_xy(x, y), expected, (x, y))
            checked += 1

    def test_larger_pellet_reaches_further(self):
        # 5.5 mm pellet: the ten reaches 0.25 + 2.75 = 3.0 mm
        self.assertEqual(score_xy(300, 0, 550).ring, 10)
        self.assertEqual(score_xy(301, 0, 550).ring, 9)
        self.assertEqual(score_xy(300, 0, 450).ring, 9)

    def test_rejects_bad_pellet(self):
        for bad in (0, -2, 451):
            with self.assertRaises(ValueError):
                score_xy(0, 0, bad)


if __name__ == "__main__":
    unittest.main()
