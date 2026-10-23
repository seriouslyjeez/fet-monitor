import json
import os
import unittest

from fetserver.lineparse import ParseError, echo_summary, normalise, parse_echo, parse_shot_line

HERE = os.path.dirname(__file__)

REAL_SHOT = ('{"shot":1, "miss":0, "name":"TARGET", "time":752.59 ,"x":-3.61, "y":-14.56 , "N":427, "E":593, '
             '"S":71, "W":0 , "V_REF":4.94, , "T":19.00, , "VERSION":"4.3.0 December 21, 2023" }')
REAL_MISS = '{"shot":2, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'
TEST18 = ('{"shot":0, "miss":0, "name":"TARGET", "time":300.08 ,"x":-7.07, "y":21.21 , "N":0, "E":0, "S":0, "W":0 , '
          '"V_REF":4.94, , "T":18.50, , "VERSION":"4.3.0 December 21, 2023" }')


class Normalise(unittest.TestCase):
    def test_real_line_was_not_valid_json(self):
        with self.assertRaises(ValueError):
            json.loads(REAL_SHOT)
        json.loads(normalise(REAL_SHOT))

    def test_collapses_doubled_leading_trailing_commas(self):
        self.assertEqual(json.loads(normalise('{,"a":1,, "b":2,,}')), {"a": 1, "b": 2})
        self.assertEqual(json.loads(normalise('{"a":[1,,2,]}')), {"a": [1, 2]})

    def test_never_touches_strings(self):
        d = json.loads(normalise('{"a":"x,,y, ,z",, "b":1}'))
        self.assertEqual(d, {"a": "x,,y, ,z", "b": 1})

    def test_escaped_quote_in_string(self):
        d = json.loads(normalise(r'{"a":"say \"hi,,\"",,"b":2}'))
        self.assertEqual(d["b"], 2)
        self.assertEqual(d["a"], 'say "hi,,"')

    def test_valid_json_unchanged_semantically(self):
        self.assertEqual(json.loads(normalise(REAL_MISS)), json.loads(REAL_MISS))


class ShotLines(unittest.TestCase):
    def test_real_shot(self):
        s = parse_shot_line(REAL_SHOT)
        self.assertEqual((s.shot, s.miss, s.x_h, s.y_h), (1, False, -361, -1456))
        self.assertEqual((s.n, s.e, s.s, s.w), (427, 593, 71, 0))
        self.assertEqual(s.time_s, 752.59)
        self.assertEqual(s.version, "4.3.0 December 21, 2023")
        self.assertEqual((s.v_ref, s.temp_c), (4.94, 19.0))

    def test_real_miss_has_no_position(self):
        s = parse_shot_line(REAL_MISS)
        self.assertTrue(s.miss)
        self.assertIsNone(s.x_h)
        self.assertIsNone(s.y_h)
        self.assertIsNone(s.time_s)
        self.assertEqual((s.e, s.s, s.face), (5596, 13199, 0))

    def test_test18_shot(self):
        s = parse_shot_line(TEST18)
        self.assertEqual((s.shot, s.x_h, s.y_h), (0, -707, 2121))

    def test_surrounding_whitespace_and_crlf(self):
        self.assertEqual(parse_shot_line("\r\n" + REAL_SHOT + "\r\n").shot, 1)

    def test_garbage(self):
        for bad in ("", "hello", '{"shot":', '{"foo":1}', '{"shot":1,"miss":0}', "[1,2]"):
            with self.subTest(bad=bad):
                with self.assertRaises(ParseError):
                    parse_shot_line(bad)


class Echo(unittest.TestCase):
    def test_real_echo_block(self):
        with open(os.path.join(HERE, "fixtures", "echo_4_3_0.txt")) as fh:
            text = fh.read()
        s = echo_summary(parse_echo(text))
        self.assertEqual(s["arduino_sn"], 302)
        self.assertEqual(s["calibre_x10"], 45)
        self.assertEqual(s["sensor_mm"], 230.0)
        self.assertEqual(s["angle"], 45)
        self.assertEqual(s["arduino_version"], "4.3.0 December 21, 2023")
        self.assertEqual(s["board_rev"], 3.2)

    def test_garbage(self):
        with self.assertRaises(ParseError):
            parse_echo("not json at all")


if __name__ == "__main__":
    unittest.main()
