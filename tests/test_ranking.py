"""Tie-breaking per rulebook (ISSF 6.15.1, CMP 8.6.1, NRA 15.3): each case is a pair that is equal up to the rule
being tested, and must be split by exactly that rule."""
import unittest

from fetserver.ranking import combine, rank, summary


def shots(spec):
    """'10.5 9.8 10.4x' -> shots; x marks an inner ten. ring = whole part."""
    out = []
    for tok in spec.split():
        inner = tok.endswith("x")
        v = float(tok.rstrip("x"))
        out.append({"tenths": round(v * 10), "ring": int(v), "inner": inner})
    return out


def order(entries, rules, decimal):
    r = rank([{"name": n, "sort_name": n, "summary": summary(shots(s))} for n, s in entries], rules, decimal)
    return [(e["name"], e["place"]) for e in r]


class Decimal(unittest.TestCase):          # ISSF 6.15.1 f and CMP 8.6.1 e are the same
    def test_total_then_last_series_then_last_shots(self):
        for rules in ("ISSF", "CMP"):
            # same total 104.0 over 10 shots... split by the last series
            a = " ".join(["10.4"] * 10) + " " + " ".join(["10.0"] * 10)       # 104.0 + 100.0
            b = " ".join(["10.0"] * 10) + " " + " ".join(["10.4"] * 10)       # 100.0 + 104.0: better LAST series
            self.assertEqual(order([("A", a), ("B", b)], rules, True), [("B", 1), ("A", 2)], rules)
            # same series totals: the last shot decides
            a = "10.0 10.5"
            b = "10.5 10.0"
            self.assertEqual(order([("A", a), ("B", b)], rules, True), [("A", 1), ("B", 2)], rules)

    def test_inner_tens_do_not_count_with_decimals(self):
        for rules in ("ISSF", "CMP"):
            a = "10.2x 10.2x 9.6"           # 30.0, two inner tens
            b = "10.0 10.0 10.0"            # 30.0, none; last shot 10.0 > 9.6
            self.assertEqual(order([("A", a), ("B", b)], rules, True), [("B", 1), ("A", 2)], rules)

    def test_unbreakable_tie_shares_the_place_alphabetically(self):
        r = order([("Zed", "10.4 10.4"), ("Abel", "10.4 10.4"), ("Mid", "9.0 9.0")], "ISSF", True)
        self.assertEqual(r, [("Abel", 1), ("Zed", 1), ("Mid", 3)])


class IssfInteger(unittest.TestCase):
    def test_inner_tens_then_series_then_shots(self):
        self.assertEqual(order([("A", "10 10 9"), ("B", "10x 10 9")], "ISSF", False), [("B", 1), ("A", 2)], "inner tens")
        a = " ".join(["10"] * 10) + " " + " ".join(["9"] * 10)
        b = " ".join(["9"] * 10) + " " + " ".join(["10"] * 10)
        self.assertEqual(order([("A", a), ("B", b)], "ISSF", False), [("B", 1), ("A", 2)], "last series")
        # same total, same inners, same single series: last shot, an inner ten above a ten
        self.assertEqual(order([("A", "10 10x 9"), ("B", "10x 9 10")], "ISSF", False), [("B", 1), ("A", 2)])
        self.assertEqual(order([("A", "10x 10"), ("B", "10 10x")], "ISSF", False), [("B", 1), ("A", 2)], "inner ten beats ten, shot by shot")
        # then decimals shot by shot (electronic targets)
        self.assertEqual(order([("A", "10.1 10.9"), ("B", "10.9 10.3")], "ISSF", False), [("A", 1), ("B", 2)], "last shot 10.9 > 10.3")


class CmpInteger(unittest.TestCase):
    def test_inner_tens_then_series_with_their_inners_then_shots(self):
        self.assertEqual(order([("A", "10 10"), ("B", "10x 10")], "CMP", False), [("B", 1), ("A", 2)])
        a = " ".join(["10"] * 10) + " " + " ".join(["9"] * 10)
        b = " ".join(["9"] * 10) + " " + " ".join(["10"] * 10)
        self.assertEqual(order([("A", a), ("B", b)], "CMP", False), [("B", 1), ("A", 2)])
        # equal last-series score; the last series with more inner tens ranks higher (8.6.1 b), totals' inners equal
        a = "10x " + " ".join(["10"] * 9) + " " + " ".join(["10"] * 10)
        b = " ".join(["10"] * 10) + " 10x " + " ".join(["10"] * 9)
        self.assertEqual(order([("A", a), ("B", b)], "CMP", False), [("B", 1), ("A", 2)])
        self.assertEqual(order([("A", "10x 10"), ("B", "10 10x")], "CMP", False), [("B", 1), ("A", 2)], "shot by shot")
        self.assertEqual(order([("A", "9 9"), ("B", "9 9")], "CMP", False), [("A", 1), ("B", 1)], "duplicate award: same place")


class Nra(unittest.TestCase):
    def test_strings_then_tens_then_nines_then_inner_tens(self):
        a = " ".join(["10"] * 10) + " " + " ".join(["9"] * 10)
        b = " ".join(["9"] * 10) + " " + " ".join(["10"] * 10)
        self.assertEqual(order([("A", a), ("B", b)], "NRA", False), [("B", 1), ("A", 2)], "last string")
        # same total and one string: more 10s wins even with fewer inner tens
        self.assertEqual(order([("A", "10x 10x 8"), ("B", "10 9 9")], "NRA", False), [("A", 1), ("B", 2)])
        self.assertEqual(order([("A", "10 10 8 8"), ("B", "10 9 9 8")], "NRA", False), [("A", 1), ("B", 2)], "10s")
        self.assertEqual(order([("A", "10 9 9 8"), ("B", "10 9 8 9")], "NRA", False), [("A", 1), ("B", 1)], "same counts: a tie")
        self.assertEqual(order([("A", "10 10x"), ("B", "10x 10x")], "NRA", False), [("B", 1), ("A", 2)], "inner tens last")


class Teams(unittest.TestCase):
    def test_team_is_the_sum_of_its_members(self):
        t1 = combine([summary(shots("10.4 10.0")), summary(shots("9.0 9.0"))])
        t2 = combine([summary(shots("10.0 10.0")), summary(shots("9.0 9.4"))])
        self.assertEqual((t1["total_tenths"], t1["tenths"]), (384, [194, 190]))
        r = rank([{"name": "T1", "summary": t1}, {"name": "T2", "summary": t2}], "ISSF", True)
        self.assertEqual([(e["name"], e["place"]) for e in r], [("T2", 1), ("T1", 2)], "equal totals: last shot 194 vs 190")

    def test_unknown_rules(self):
        with self.assertRaises(ValueError):
            rank([{"summary": summary([])}], "FIFA", True)



class OrderOfTheRules(unittest.TestCase):
    """Pairs where two rules point in opposite directions: the rulebook's order must decide."""
    def test_issf_inner_tens_before_the_last_series(self):
        a = " ".join(["10x"] * 10) + " " + " ".join(["9"] * 10)       # 190, 10 inner tens, weak last series
        b = " ".join(["9"] * 10) + " " + " ".join(["10"] * 10)        # 190, none, strong last series
        self.assertEqual(order([("A", a), ("B", b)], "ISSF", False), [("A", 1), ("B", 2)])
        self.assertEqual(order([("A", a), ("B", b)], "CMP", False), [("A", 1), ("B", 2)])
        self.assertEqual(order([("A", a), ("B", b)], "NRA", False), [("B", 1), ("A", 2)], "NRA: strings first, inner tens last")

    def test_cmp_series_inner_tens_before_shot_by_shot(self):
        # equal totals, equal total inner tens (2 each), equal series scores; A's last series has 2 inner tens early,
        # B's has 1 inner ten on the very last shot (shot by shot would favour B)
        a = " ".join(["10"] * 10) + " 10x 10x " + " ".join(["10"] * 8)
        b = "10x " + " ".join(["10"] * 9) + " " + " ".join(["10"] * 9) + " 10x"
        self.assertEqual(order([("A", a), ("B", b)], "CMP", False), [("A", 1), ("B", 2)])

    def test_nra_tens_before_inner_tens_and_counted_from_the_top(self):
        self.assertEqual(order([("A", "10 10 8"), ("B", "10x 9 9")], "NRA", False), [("A", 1), ("B", 2)], "two 10s beat one inner ten")
        self.assertEqual(order([("A", "10 10 7 7"), ("B", "10 9 9 6")], "NRA", False), [("A", 1), ("B", 2)], "count 10s first, not 6s")

    def test_longer_list_wins_when_the_other_is_its_prefix(self):
        from fetserver.ranking import _cmp
        self.assertEqual((_cmp([5, 3], [5, 3, 1]), _cmp([5, 3, 1], [5, 3]), _cmp([5, 3], [5, 3])), (-1, 1, 0))
        self.assertEqual(_cmp((1, [2, 3]), (1, [2, 4])), -1)


if __name__ == "__main__":
    unittest.main()
