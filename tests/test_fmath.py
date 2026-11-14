"""fetcore/fmath.py: fdlibm's exp, log, sin, cos, atan, atan2, acos in exact operations (the same bits everywhere).
Accuracy: exp and log under 1 ulp from a 60-digit reference (decimal); sin, cos, atan, atan2, acos within 1 ulp of
the C library's (itself under 1 ulp). Special values and errors as `math`. The same bits on MicroPython and on the
CYD: tests/test_micropython.py, and docs/PLAN-cyd.md (27,999 values, 2026-11-13).

Checked 2026-11-13: every fdlibm constant here equals netlib's fdlibm 5.3 source (read as text); each function's
code read against it. Mutation checks (python -B, bytecode caching off): moving each of the 78 constants by one ulp,
31 are caught by the golden bits; for the other 47 no input in 30,000 per constant changes any result (high-order
polynomial terms, thresholds), i.e. the same function in practice. Operator mutations: 346 of 413 caught; the rest
are fdlibm's exact high-word cut-offs (both branches valid there), NaN propagation (x+x or x-x), additions below half
an ulp (pi + 2*pio2_lo), the scaling for values beyond 2**+-500, or extra reduction passes that change nothing."""
import math
import os
import random
import struct
import unittest
from decimal import Decimal, localcontext

from fetcore import fmath as F


def ulps(got, ref):
    return abs(Decimal(got) - ref) / Decimal(math.ulp(float(ref)))


class Accuracy(unittest.TestCase):
    rng = random.Random(20261113)

    def test_exp_under_one_ulp(self):
        xs = [self.rng.uniform(-745, 709) for _ in range(1500)] + [self.rng.uniform(-2, 2) for _ in range(1500)]
        xs += [0.3465735902799726, -0.3465735902799726, 1.0397207708399179, 1e-9, -1e-9, 2.0 ** -30]   # branch edges
        with localcontext() as ctx:
            ctx.prec = 60
            worst = max(ulps(F.exp(x), Decimal(x).exp()) for x in xs if x > -708)
        self.assertLess(worst, 1)

    def test_log_under_one_ulp(self):
        xs = [10 ** self.rng.uniform(-307, 308) for _ in range(1500)] + [self.rng.uniform(0.5, 2) for _ in range(1500)]
        xs += [1.0 + 2.0 ** -21, 1.0 - 2.0 ** -21, 2.0 ** -1060, 0.7071067811865476, 1.4142135623730951, 273.15, 308.15]
        with localcontext() as ctx:
            ctx.prec = 60
            worst = max(ulps(F.log(x), Decimal(x).ln()) for x in xs)
        self.assertLess(worst, 1)

    def check_near_math(self, name, args):
        f, g = getattr(F, name), getattr(math, name)
        same = 0
        for a in args:
            got, ref = f(*a), g(*a)
            self.assertLessEqual(abs(got - ref), math.ulp(ref), (name, a))
            same += got == ref
        self.assertGreater(same / len(args), 0.75, name)       # and mostly identical: a wrong constant shows here

    def test_sin_cos(self):
        xs = [(self.rng.uniform(-7, 7),) for _ in range(3000)] + [(self.rng.uniform(-8e5, 8e5),) for _ in range(1000)]
        xs += [(v,) for v in (0.7853981633974483, 2.356194490192345, 1.5707963267948966, 3.141592653589793, 1e-30, 0.3)]
        self.check_near_math("sin", xs)
        self.check_near_math("cos", xs)

    def test_atan_atan2_acos(self):
        self.check_near_math("atan", [(self.rng.uniform(-50, 50),) for _ in range(3000)] + [(0.44,), (0.69,), (1.19,), (2.44,), (1e30,)])
        self.check_near_math("atan2", [(self.rng.uniform(-200, 200), self.rng.uniform(-200, 200)) for _ in range(3000)])
        self.check_near_math("acos", [(self.rng.uniform(-1, 1),) for _ in range(3000)] + [(0.5,), (-0.5,), (1e-60,)])

    def test_hypot_under_one_ulp(self):
        xs = [(self.rng.uniform(-300, 300), self.rng.uniform(-300, 300)) for _ in range(3000)]
        xs += [(10 ** self.rng.uniform(-300, 300), 10 ** self.rng.uniform(-300, 300)) for _ in range(1000)]
        with localcontext() as ctx:
            ctx.prec = 60
            worst = max(ulps(F.hypot(x, y), (Decimal(x) ** 2 + Decimal(y) ** 2).sqrt()) for x, y in xs)
        self.assertLess(worst, 1)                   # (this Mac's Python 3.9 math.hypot: up to 1.9 ulps)
        for x, y, want in ((3.0, 4.0, 5.0), (0.0, 0.0, 0.0), (-0.0, 5.0, 5.0), (math.inf, math.nan, math.inf), (5e-324, 0.0, 5e-324)):
            self.assertEqual(F.hypot(x, y), want)
        self.assertTrue(math.isnan(F.hypot(math.nan, 1.0)))
        self.assertEqual(F.hypot(1e308, 1e308), 1.4142135623730951e+308)

    def test_radians_degrees(self):
        self.assertEqual(F.radians(45.0), math.radians(45.0))
        self.assertEqual(F.degrees(1.0), math.degrees(1.0))


GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "fmath_golden.txt")


def golden_mismatches(module):
    """Lines of tests/fixtures/fmath_golden.txt (tools/fmath_golden.py) whose result differs from `module`'s."""
    d = lambda s: struct.unpack("<d", bytes.fromhex(s))[0]
    bad = []
    with open(GOLDEN) as fh:
        for line in fh:
            p = line.split()
            got = getattr(module, p[0])(*[d(v) for v in p[1:-1]])
            if struct.pack("<d", got).hex() != p[-1]:
                bad.append(line.strip())
    return bad


class Golden(unittest.TestCase):
    def test_same_bits_as_recorded(self):
        """The exact bits, every branch: what makes a shot's position the same on the Pi, deepTENS and the CYD."""
        bad = golden_mismatches(F)
        self.assertEqual(bad[:5], [], "%d of the recorded values differ" % len(bad))


class SpecialValues(unittest.TestCase):
    def test_exact_points(self):
        self.assertEqual(F.exp(0.0), 1.0)
        self.assertEqual(F.log(1.0), 0.0)
        self.assertEqual(F.sin(0.0), 0.0)
        self.assertEqual(F.cos(0.0), 1.0)
        self.assertEqual(F.acos(1.0), 0.0)
        self.assertEqual(F.acos(-1.0), math.pi)
        self.assertEqual(F.exp(-800.0), 0.0)
        self.assertEqual(F.exp(-math.inf), 0.0)
        self.assertEqual(F.exp(math.inf), math.inf)

    def test_atan2_signs_and_axes(self):
        for y, x in [(0.0, 1.0), (-0.0, 1.0), (0.0, -1.0), (-0.0, -1.0), (1.0, 0.0), (-1.0, 0.0), (5.0, 1.0), (1e-300, -1e300),
                     (1e300, 1e-300), (math.inf, math.inf), (-math.inf, math.inf), (math.inf, -math.inf), (-math.inf, -math.inf),
                     (1.0, -math.inf), (-1.0, -math.inf), (1.0, math.inf), (-1.0, math.inf), (math.inf, 1.0), (-math.inf, 1.0), (math.inf, 2.0), (-math.inf, -2.0)]:
            got, ref = F.atan2(y, x), math.atan2(y, x)
            self.assertEqual(got, ref, (y, x))
            self.assertEqual(math.copysign(1, got), math.copysign(1, ref), (y, x))
        self.assertTrue(math.isnan(F.atan2(math.nan, 1.0)))

    def test_errors_as_math(self):
        for f, v in ((F.log, 0.0), (F.log, -1.0), (F.acos, 1.0000000000000002), (F.acos, -2.0), (F.sin, math.inf), (F.cos, -math.inf)):
            with self.assertRaises(ValueError):
                f(v)
        with self.assertRaises(OverflowError):
            F.exp(710.0)
        with self.assertRaises(OverflowError):
            F.exp(math.nextafter(F._O_THRESHOLD, math.inf))     # the same high word as the largest finite result
        self.assertEqual(F.exp(F._O_THRESHOLD), math.exp(F._O_THRESHOLD))
        with self.assertRaises(ValueError):
            F.sin(1e7)                              # beyond fdlibm's medium reduction: never a shot's angle
        for f in (F.exp, F.log, F.sin, F.cos, F.atan, F.acos):     # nan passes through, as math (upstream_solve needs it)
            self.assertTrue(math.isnan(f(math.nan)), f.__name__)
            self.assertTrue(math.isnan(getattr(math, f.__name__)(math.nan)), f.__name__)
        self.assertTrue(math.isnan(F.exp(math.nan)))
        self.assertTrue(math.isnan(F.log(math.nan)))
        self.assertEqual(F.log(math.inf), math.inf)


if __name__ == "__main__":
    unittest.main()
