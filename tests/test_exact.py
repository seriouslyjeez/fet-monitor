"""fetcore/exact.py: decimal text read exactly (the same on CPython and MicroPython): to_float gives CPython's float(),
to_hundredths gives what decimal's ROUND_HALF_UP gave, loads gives json.loads with non-integer numbers kept as text."""
import json
import random
import unittest
from decimal import Decimal, ROUND_HALF_UP

from fetcore.exact import Dec, loads, parts, to_float, to_hundredths


def old_hundredths(value):                       # fetcore/scoring.py before 2026-11-13
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def decimals(rng, n):
    for _ in range(n):
        digits = rng.randint(1, 16)
        m = rng.randrange(10 ** digits)
        point = rng.randint(0, digits)
        s = str(m).rjust(point + 1, "0")
        s = s[:len(s) - point] + ("." + s[len(s) - point:] if point else "")
        yield ("-" if rng.random() < 0.5 else "") + s


class Numbers(unittest.TestCase):
    rng = random.Random(20261113)

    def test_to_float_is_floats(self):
        texts = list(decimals(self.rng, 20000)) + ["0.1", "-16.99", "31.48", "3.615", "1e22", "1.5e-7", "-0", "0.0",
                                                  "123456789012345.6", "9007199254740991", "2.2250738585072014e-308",
                                                  "9007199254740993", "1.00000000000000011102230246251565404236316680908203125",
                                                  "1.000000000000000111022302462515654042363166809082031251", "1.7976931348623157e308",
                                                  "123456789012345678901234567890", "0.30000000000000004", "8.98846567431158e307"]
        rng = random.Random(7)
        texts += ["%.17g" % rng.uniform(-1e6, 1e6) for _ in range(5000)] + ["%.25e" % 10 ** rng.uniform(-300, 300) for _ in range(5000)]
        for t in texts:
            self.assertEqual(repr(to_float(t)), repr(float(t)), t)

    def test_to_hundredths_as_before(self):
        texts = list(decimals(self.rng, 20000)) + ["0.005", "-0.005", "0.015", "2.675", "-2.675", "1e-3", "1.25E+1",
                                                  "0.00499999", "-3.61", "10", "-0"]
        for t in texts:
            self.assertEqual(to_hundredths(t), old_hundredths(Decimal(t)), t)
            self.assertEqual(to_hundredths(Dec(t)), old_hundredths(Decimal(t)), t)
        for v in (3.61, -16.99, 0.125, 2.675, 1e-7, 5):
            self.assertEqual(to_hundredths(v), old_hundredths(v), v)

    def test_rejects(self):
        for bad in ("", "-", ".", "1.2.3", "1e", "abc", "1,5", "--1", "1e+-2"):
            with self.assertRaises(ValueError):
                parts(bad)
        for bad in (float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                to_hundredths(bad)

    def test_beyond_normal_doubles_as_float(self):          # overflow to inf, subnormals rounded, as float()
        rng = random.Random(11)
        texts = ["1e400", "-1e400", "1e-400", "2e-310", "-4.9e-324", "2.4703282292062327e-324", "2.4703282292062328e-324",
                 "1.7976931348623158e308", "1.7976931348623159e308", "4.1e394", "2.2250738585072011e-308"]
        texts += ["%.20e" % (rng.random() * 10 ** rng.uniform(-330, -300)) for _ in range(3000)]
        for t in texts:
            self.assertEqual(repr(to_float(t)), repr(float(t)), t)


class Json(unittest.TestCase):
    def same(self, text):
        want = json.loads(text, parse_float=Dec)
        self.assertEqual(loads(text), want, text)

    def test_as_json_loads(self):
        for t in ['{"shot":3, "miss":0, "x":-3.61, "y":-14.56, "N":427, "T":19.0, "VERSION":"4.3.0"}',
                  '[1, -2, 3.5e2, -0.0, 0, true, false, null, "a\\"b\\\\c\\u00e9\\ud83d\\ude00\\n"]',
                  ' { "a" : { "b" : [ ] , "c" : { } } } ', '"x"', '12', '-1.5E-3', '{"a":1,"a":2}']:
            self.same(t)
        nan = loads('[NaN, Infinity, -Infinity]')
        self.assertTrue(nan[0] != nan[0] and nan[1] == float("inf") and nan[2] == float("-inf"))

    def test_rejects_as_json_loads(self):
        for t in ['{"a":1,}', '[1,]', '{"a" 1}', '01', '1.', '.5', '+1', '"\\x"', '"a\nb"', '{"a":1} x', '', 'nan',
                  '{a:1}', '[1 2]', '"abc', '-', '1e']:
            with self.assertRaises(ValueError, msg=t):
                json.loads(t)
            with self.assertRaises(ValueError, msg=t):
                loads(t)


if __name__ == "__main__":
    unittest.main()
