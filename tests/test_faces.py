"""Target faces (fetserver/faces.py) against the rulebooks: the CMP decimal maximum-radii chart (air rifle and air
pistol), the ISSF and NRA ring tables at their edges, the inner ten / X rules, and exactly the scoring the 10 m air
rifle face had before faces existed (fetserver/scoring.py)."""
import random
import unittest

from fetserver import faces as F
from fetserver.scoring import is_inner_ten, score_xy


def at(face, r_mm_h, p=None):
    """The score of a shot r hundredths of a mm right of the centre."""
    return F.score(F.FACES[face], r_mm_h, 0, p)


class CmpChart(unittest.TestCase):
    """CMP Air Gun Rules 2026, Decimal Scoring Ring Maximum Radii (mm), read off the chart."""
    AR = {109: 0.25, 105: 1.25, 100: 2.50, 99: 2.75, 90: 5.00, 70: 10.00, 61: 12.25, 50: 15.00, 30: 20.00, 21: 22.25, 19: 22.75, 10: 25.00}
    AP = {109: 0.80, 103: 5.60, 100: 8.00, 90: 16.00, 74: 28.80, 60: 40.00, 45: 52.00, 29: 64.80, 10: 80.00}

    def check(self, face, chart):
        for tenths, r in chart.items():
            h = round(r * 100)
            self.assertEqual(at(face, h).tenths, tenths, (face, r, "at the maximum radius"))
            nxt = at(face, h + 1)
            self.assertEqual(nxt.tenths, tenths - 1 if tenths > 10 else 0, (face, r, "just outside"))

    def test_air_rifle(self):
        self.check("issf-ar10", self.AR)

    def test_air_pistol(self):
        self.check("issf-ap10", self.AP)

    def test_every_tenth_follows_the_chart(self):
        for t in range(10, 110):
            self.assertEqual(at("issf-ar10", (110 - t) * 25).tenths, t)        # 0.25 mm a tenth
            self.assertEqual(at("issf-ap10", (110 - t) * 80).tenths, t)        # 0.80 mm a tenth


class SameAsBefore(unittest.TestCase):
    def test_air_rifle_exactly_as_scoring_py(self):
        rnd = random.Random(7)
        pts = [(rnd.randint(-2600, 2600), rnd.randint(-2600, 2600)) for _ in range(20000)]
        pts += [(x, 0) for x in range(0, 2600)] + [(x, x) for x in range(0, 1900)]
        for x, y in pts:
            old, new = score_xy(x, y), F.score(F.FACES["issf-ar10"], x, y)
            self.assertEqual((old.ring, old.tenths), (new.ring, new.tenths), (x, y))
            self.assertEqual(new.inner, old.ring > 0 and is_inner_ten(old.tenths), (x, y))
        for proj in (450, 572, 560):                                          # an ammo test's projectile (h)
            for x in range(0, 2700, 7):
                old, new = score_xy(x, 0, proj), F.score(F.FACES["issf-ar10"], x, 0, proj * F.TU_PER_H)
                self.assertEqual((old.ring, old.tenths), (new.ring, new.tenths), (proj, x))


class IssfFaces(unittest.TestCase):
    def test_50m_rifle(self):
        self.assertEqual((at("issf-r50", 800).tenths, at("issf-r50", 801).tenths), (100, 99), "the ten reaches 5.2 + 2.8 mm")
        self.assertEqual(at("issf-r50", 80).tenths, 109)
        self.assertEqual((at("issf-r50", 530).inner, at("issf-r50", 531).inner), (True, False), "inner ten: touches the 5 mm ring")
        self.assertEqual((at("issf-r50", 8000).ring, at("issf-r50", 8001).ring), (1, 0), "the 1 ring: 77.2 + 2.8 mm")

    def test_precision_pistol(self):
        self.assertEqual((at("issf-p25", 2780).tenths, at("issf-p25", 2781).tenths), (100, 99), "25 + 2.8 mm")
        self.assertEqual((at("issf-p25", 1530).inner, at("issf-p25", 1531).inner), (True, False), "inner ten 25 mm: 12.5 + 2.8")
        self.assertEqual(F.FACES["issf-p25"].ring_step, F.mm("25"))

    def test_ring_widths(self):
        self.assertEqual([F.FACES[f].ring_step for f in ("issf-ar10", "issf-ap10", "issf-r50")], [F.mm("2.5"), F.mm("8"), F.mm("8")])
        self.assertEqual(F.FACES["nra-a23"].ring_step, F.inch("0.5"))

    def test_air_pistol_inner_ten(self):
        self.assertEqual((at("issf-ap10", 475).inner, at("issf-ap10", 476).inner), (True, False), "2.5 + 2.25 mm")
        self.assertTrue(at("issf-ap10", 476).ring == 10)

    def test_air_rifle_inner_ten_covers_the_dot(self):
        self.assertEqual((at("issf-ar10", 200).inner, at("issf-ar10", 201).inner), (True, False), "2.25 - 0.25 mm")


class NraFaces(unittest.TestCase):
    """Inches are exact: 0.001 in = 254 tu. The .22 gauge is 0.224 in (5.6896 mm)."""
    def test_a23_x_ten_and_last_ring(self):
        # X: 0.195 + 0.112 in = 7.7978 mm; ten: 0.445 + 0.112 in = 14.1478 mm; 5 ring: 2.945 + 0.112 in = 77.6478 mm
        self.assertEqual((at("nra-a23", 779).inner, at("nra-a23", 780).inner), (True, False))
        self.assertEqual((at("nra-a23", 1414).ring, at("nra-a23", 1415).ring), (10, 9))
        self.assertEqual((at("nra-a23", 7764).ring, at("nra-a23", 7765).ring), (5, 0), "rings 10 to 5 only")

    def test_a17_center_shots(self):
        # ten: 0.075 + 0.112 in = 4.7498 mm; center: clear of the nine ring, r < 0.2415 - 0.112 in = 3.2893 mm
        self.assertEqual((at("nra-a17", 474).ring, at("nra-a17", 475).ring), (10, 9))
        self.assertEqual((at("nra-a17", 328).inner, at("nra-a17", 329).inner), (True, False))
        self.assertFalse(F.FACES["nra-a17"].decimal, "conventional: whole rings")

    def test_reduced_targets(self):
        self.assertEqual((at("nra-usas50", 322).ring, at("nra-usas50", 323).ring), (10, 9), "0.38 + 2.8448 mm")
        self.assertFalse(at("nra-usas50", 0).inner, "no center shots on the NRA/USAS-50")
        self.assertEqual((at("nra-a51", 489).inner, at("nra-a51", 490).inner), (True, False), "2.048 + 2.8448 mm")
        self.assertEqual(F.FACES["issf-r50"].rings[0][1], F.mm("10.4"), "A-50 is the ISSF 50 m target (NRA 4.10)")


# The rulebooks' own tables, copied as pdftotext printed them (ISSF Rule Book 2026 6.3.4; NRA Smallbore Rifle Rules
# 2026 4.2-4.10), so a typing slip in faces.py cannot match by accident.
RULEBOOK = {
    "issf-r50": """10 Ring 10.4mm (±0.1mm) 5 Ring 90.4mm (±0.5mm) 9 Ring 26.4mm (±0.1mm) 4 Ring 106.4mm (±0.5mm)
                   8 Ring 42.4mm (±0.2mm) 3 Ring 122.4mm (±0.5mm) 7 Ring 58.4mm (±0.5mm) 2 Ring 138.4mm (±0.5mm)
                   6 Ring 74.4mm (±0.5mm) 1 Ring 154.4mm (±0.5mm)""",
    "issf-ar10": """10 Ring 0.5mm (±0.1mm) 5 Ring 25.5mm (±0.1mm) 9 Ring 5.5mm (±0.1mm) 4 Ring 30.5mm (±0.1mm)
                    8 Ring 10.5mm (±0.1mm) 3 Ring 35.5mm (±0.1mm) 7 Ring 15.5mm (±0.1mm) 2 Ring 40.5mm (±0.1mm)
                    6 Ring 20.5mm (±0.1mm) 1 Ring 45.5mm (±0.1mm)""",
    "issf-p25": """10 Ring 50mm (±0.2mm) 5 Ring 300mm (±1.0mm) 9 Ring 100mm (±0.4mm) 4 Ring 350mm (±1.0mm)
                   8 Ring 150mm (±0.5mm) 3 Ring 400mm (±2.0mm) 7 Ring 200mm (±1.0mm) 2 Ring 450mm (±2.0mm)
                   6 Ring 250mm (±1.0mm) 1 Ring 500mm (±2.0mm)""",
    "issf-ap10": """10 Ring 11.5mm (±0.1mm) 5 Ring 91.5mm (±0.5mm) 9 Ring 27.5mm (±0.1mm) 4 Ring 107.5mm (±0.5mm)
                    8 Ring 43.5mm (±0.2mm) 3 Ring 123.5mm (±0.5mm) 7 Ring 59.5mm (±0.5mm) 2 Ring 139.5mm (±0.5mm)
                    6 Ring 75.5mm (±0.5mm) 1 Ring 155.5mm (±0.5mm)""",
    "nra-a17": """10 ring............... 150 inch 7 ring............. 1.150 inches
                  9 ring............... 483 inch 6 ring............. 1.483 inches
                  8 ring............... 817 inch 5 ring............. 1.817 inches""",
    "nra-usas50": """10 ring...................... 0.76 mm 6 ring. .................. 18.75 mm
                     9 ring...................... 4.12 mm 5 ring. .................. 23.63 mm
                     8 ring...................... 9.00 mm 4 ring. .................. 28.50 mm
                     7 ring...................... 13.87 mm 3 ring. .................. 33.38 mm""",
    "nra-a23": """X ring...................... .39 inch 7 ring............. 3.89 inches
                  10 ring..................... .89 inch 6 ring............. 4.89 inches
                  9 ring................... 1.89 inches 5 ring............. 5.89 inches
                  8 ring................... 2.89 inches""",
    "nra-a27": """X ring...................... ..359 inch 7 ring............. 2.879 inches
                  10 ring..................... .719 inch 6 ring............. 3.599 inches
                  9 ring................... 1.439 inches 5 ring............. 4.319 inches
                  8 ring................... 2.159 inches 4 ring............. 5.038 inches""",
    "nra-a51": """Inner 10 ring. ........ 4.096 mm 5 ring........... 82.186 mm
                  10 ring................... 9.034 mm 4 ring........... 96.816 mm
                  9 ring................. 23.664 mm 3 ring......... 111.447 mm
                  8 ring................. 38.295 mm 2 ring......... 126.077 mm
                  7 ring................. 52.925 mm 1 ring......... 140.708 mm
                  6 ring................. 67.556 mm""",
    "nra-a26": """X ring...................... .393 inch 7 ring............. 3.148 inches
                  10 ring..................... .787 inch 6 ring............. 3.936 inches
                  9 ring................... 1.574 inches 5 ring............. 4.723 inches
                  8 ring................... 2.361 inches 4 ring............. 5.510 inches""",
}


class AgainstTheRulebooks(unittest.TestCase):
    def parse(self, text):
        import re
        out, inner = {}, None
        for m in re.finditer(r"(Inner 10|X|\d+) [Rr]ing[ .]*?(\.*\d*\.?\d+)\s*(mm|inch|inches)", text):
            name, num, unit = m.group(1), m.group(2).lstrip("."), m.group(3)
            if "." not in num and unit != "mm":
                num = "0." + num                               # pdftotext lost the point: "150 inch" is .150 in
            elif m.group(2).startswith(".") and unit != "mm":
                num = "0." + num if not num.startswith("0") else num
            v = F.mm(num) if unit == "mm" else F.inch(num)
            if name in ("X", "Inner 10"):
                inner = v
            else:
                out[int(name)] = v
        return out, inner

    def test_every_ring_as_printed(self):
        for fid, text in RULEBOOK.items():
            rings, inner = self.parse(text)
            f = F.FACES[fid]
            self.assertEqual(rings, dict(f.rings), fid)
            if inner is not None:
                self.assertEqual(inner, f.inner[1], fid + ": inner ten / X ring")
        self.assertEqual(set(RULEBOOK), set(F.FACES), "every face checked")


class Library(unittest.TestCase):
    def test_every_face_is_consistent(self):
        for f in F.FACES.values():
            self.assertEqual(f.rings[0][0], 10, f.id)
            self.assertGreater(f.black, 0, f.id)
            self.assertTrue(f.rule, f.id)
            v = F.view(f)
            self.assertEqual([r["value"] for r in v["rings"]], [x for x, _ in f.rings])

    def test_refusals(self):
        with self.assertRaises(ValueError):
            F.mm("0.00001")
        with self.assertRaises(ValueError):
            F.Face("x", "x", "x", ((10, 100), (8, 200)), F.PELLET, 1)              # a gap
        with self.assertRaises(ValueError):
            F.Face("x", "x", "x", ((10, 200), (9, 100)), F.PELLET, 1)              # shrinking
        with self.assertRaises(ValueError):
            F.score(F.FACES["issf-ar10"], 0, 0, 3)                                 # odd projectile

    def test_unknown_face_means_air_rifle(self):
        self.assertEqual(F.face(None).id, "issf-ar10")
        self.assertEqual(F.face("nope").id, "issf-ar10")


if __name__ == "__main__":
    unittest.main()
