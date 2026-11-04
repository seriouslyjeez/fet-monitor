"""Target faces: the scoring rings of each discipline's official target, and exact scoring on any of them.

Every dimension is the rulebook's own number, kept exact: lengths are integers in tenths of a micrometre ("tu",
1e-7 m), where both 0.01 mm (ISSF, metric NRA targets) and 0.001 inch (NRA conventional targets) are whole numbers
(100 tu and 254 tu). Shots arrive in hundredths of a millimetre (h = 100 tu) and are compared with squared radii, so
there is no floating point at a ring line and the same shot always gets the same score.

Scoring, for every face (ISSF 6.3.3, CMP AG 8.1.3 and its maximum-radii chart, NRA Smallbore 14.3 a):
* A shot scores the highest ring its hole touches: pellet-centre radius <= ring outer radius + projectile radius.
* Decimal scores divide each ring band into ten equal parts (10.9 at the centre), as in ISSF 6.3.3.1; the ten's band
  runs from the centre out to the ten's reach. Faces scored in whole rings (NRA conventional) still get the decimal
  value, which a match in whole rings ignores.
* The inner ten / X is decided from the shot's position, never from its decimal value (that only works on the
  10 m air rifle face), see INNER kinds below.

Sources (the publishers' documents, see rulebooks.py DOCS; the NRA smallbore rulebook 2026, sha256 f48a116c...):
ISSF Rule Book 2026 6.3.4.2 (50 m rifle), 6.3.4.3 (10 m air rifle), 6.3.4.5 (25/50 m precision pistol), 6.3.4.6
(10 m air pistol); CMP Air Gun Rules
2026 6.2.1 and the Decimal Scoring Ring Maximum Radii chart; NRA Smallbore Rifle Rules 2026 4.2-4.10 and 14.3; NRA
International Pistol Rules 2025 (B-40 = the ISSF air pistol target).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Optional, Tuple

TU_PER_MM = 10_000
TU_PER_INCH = 254_000
TU_PER_H = 100                     # a shot's coordinates: hundredths of a millimetre


def mm(v: str) -> int:
    """A length in millimetres, as printed in the rulebook, to tu (exact)."""
    t = Decimal(v) * TU_PER_MM
    if t != t.to_integral_value():
        raise ValueError(f"{v} mm is finer than 0.1 um")
    return int(t)


def inch(v: str) -> int:
    t = Decimal(v) * TU_PER_INCH
    if t != t.to_integral_value():
        raise ValueError(f"{v} in is finer than 0.1 um")
    return int(t)


# How the inner ten (X, center shot) is decided:
#   "touch":   the shot hole touches the inner ring (inward gauge): r <= inner radius + projectile radius
#              (ISSF 50 m rifle and air pistol inner ten; NRA X-rings 14.3 c; A-50/A-51 center shots 14.3 f 3)
#   "cover":   the shot hole covers the inner dot completely: r <= projectile radius - dot radius
#              (10 m air rifle: ISSF 6.3.4.3, CMP AG 6.2.1 a: decimal 10.2 or more)
#   "a17":     NRA A-17 center shot (14.3 f 1): the hole touches the ten ring but not the outside edge of the
#              nine ring: r <= ten radius + projectile radius and r + projectile radius < nine radius
INNER_KINDS = ("touch", "cover", "a17")


@dataclass(frozen=True)
class Face:
    id: str
    name: str
    rule: str                                   # where the dimensions come from
    rings: Tuple[Tuple[int, int], ...]          # (ring value, outer diameter in tu), 10 first, down to the lowest ring
    projectile: int                             # the projectile (gauge) diameter this face is scored with, tu
    black: int                                  # diameter of the aiming black, tu
    inner: Optional[Tuple[str, int]] = None     # (kind, diameter tu); None: no inner ten / X on this face
    white_ten: bool = False                     # the ten is a white dot in the black (10 m air rifle)
    decimal: bool = True                        # the rulebook scores this face in tenths on electronic targets
    unit: str = "mm"                            # the unit the rulebook gives it in (for showing its dimensions)
    note: str = ""
    bull: int = 10                              # record shots per bull on the paper target (NRA tie-breaks by bull, 15.7)
    higher: Optional[str] = None                # X's of higher value (NRA 14.3 e, g): "inside" the X ring, or the hole "covers" the ten
    by_value: Dict[int, int] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        vals = [v for v, _ in self.rings]
        dias = [d for _, d in self.rings]
        if vals != list(range(10, 10 - len(vals), -1)) or not vals:
            raise ValueError(f"{self.id}: rings must be 10, 9, ... without gaps")
        if any(a >= b for a, b in zip(dias, dias[1:])) or dias[0] <= 0:
            raise ValueError(f"{self.id}: ring diameters must grow outwards")
        if self.inner is not None and (self.inner[0] not in INNER_KINDS or self.inner[1] <= 0):
            raise ValueError(f"{self.id}: unknown inner ten")
        if self.projectile <= 0 or self.projectile % 2:
            raise ValueError(f"{self.id}: projectile diameter must be a positive even number of tu")
        self.by_value.update(self.rings)

    @property
    def lowest(self) -> int:
        return self.rings[-1][0]

    @property
    def ring_step(self) -> int:
        """The width of one ring (radius step from the 9 to the 8 ring), tu: what "clicks per ring" means here."""
        return (self.by_value[8] - self.by_value[9]) // 2 if 8 in self.by_value else self.by_value[9] // 2


@dataclass(frozen=True)
class FaceScore:
    ring: int          # 0 = a miss (off the scoring rings)
    tenths: int        # decimal x 10 (0 for a miss); 109 at the centre
    inner: bool        # inner ten / X / center shot

    @property
    def decimal(self) -> float:
        return self.tenths / 10.0


MISS = FaceScore(0, 0, False)


def reach(face: Face, ring: int, projectile: Optional[int] = None) -> int:
    """Largest pellet-centre radius (tu) that still scores `ring` (inclusive)."""
    p = face.projectile if projectile is None else projectile
    return face.by_value[ring] // 2 + p // 2


def score(face: Face, x_h: int, y_h: int, projectile: Optional[int] = None) -> FaceScore:
    """Score a shot at (x_h, y_h) hundredths of a mm from the centre of `face`."""
    p = face.projectile if projectile is None else projectile
    if p <= 0 or p % 2:
        raise ValueError("projectile diameter must be a positive even number of tu")
    x, y = x_h * TU_PER_H, y_h * TU_PER_H
    r2 = x * x + y * y
    lo = face.lowest
    for ring in range(10, lo - 1, -1):                            # innermost first
        outer = reach(face, ring, p)
        if r2 <= outer * outer:
            inner_edge = reach(face, ring + 1, p) if ring < 10 else 0
            width = outer - inner_edge
            for j in range(10):                                   # smallest j with r <= inner_edge + (j + 1) * width / 10
                limit = 10 * inner_edge + (j + 1) * width
                if 100 * r2 <= limit * limit:
                    return FaceScore(ring, ring * 10 + 9 - j, ring == 10 and _inner(face, r2, p))
    return MISS


def _inner(face: Face, r2: int, p: int) -> bool:
    if face.inner is None:
        return False
    kind, dia = face.inner
    if kind == "touch":
        lim = dia // 2 + p // 2
        return r2 <= lim * lim
    if kind == "cover":
        lim = p // 2 - dia // 2
        return lim >= 0 and r2 <= lim * lim
    # a17: touches the ten ring (already true: this is a ten) and the hole stays clear of the nine ring's outside edge
    lim = face.by_value[9] // 2 - p // 2
    return lim > 0 and r2 < lim * lim


def higher(face: Face, x_h: int, y_h: int, projectile: Optional[int] = None) -> bool:
    """An X of higher value (NRA Smallbore 14.3 e: an X whose hole does not touch the outside edge of the X ring) or a
    center shot of higher value (14.3 g, A-17: the hole obliterates the ten ring). Only for ties; never a score."""
    if face.higher is None or face.inner is None:
        return False
    p = face.projectile if projectile is None else projectile
    x, y = x_h * TU_PER_H, y_h * TU_PER_H
    r2 = x * x + y * y
    if face.higher == "inside":                                   # r + p/2 < X radius
        lim = face.inner[1] // 2 - p // 2
        return lim > 0 and r2 < lim * lim
    lim = p // 2 - face.by_value[10] // 2                         # covers: the ten ring inside the hole
    return lim >= 0 and r2 <= lim * lim


def _rings(unit, *dias):
    conv = mm if unit == "mm" else inch
    return tuple((10 - i, conv(d)) for i, d in enumerate(dias))


PELLET = mm("4.5")                 # .177 air gun pellet
RIMFIRE_ISSF = mm("5.6")           # .22 the ISSF electronic targets score with (calibre 5.6 mm)
RIMFIRE_NRA = inch("0.224")        # NRA .22 scoring gauge, 0.2225-0.2240 in (Smallbore 14.3 a): its upper limit

FACES: Dict[str, Face] = {f.id: f for f in (
    Face("issf-ar10", "10 m Air Rifle (ISSF)", "ISSF 6.3.4.3; CMP AG 6.2.1 a",
         _rings("mm", "0.5", "5.5", "10.5", "15.5", "20.5", "25.5", "30.5", "35.5", "40.5", "45.5"),
         PELLET, mm("30.5"), ("cover", mm("0.5")), white_ten=True),
    Face("issf-ap10", "10 m Air Pistol (ISSF, NRA B-40)", "ISSF 6.3.4.6; CMP AG 6.2.1 b; NRA International Pistol B-40",
         _rings("mm", "11.5", "27.5", "43.5", "59.5", "75.5", "91.5", "107.5", "123.5", "139.5", "155.5"),
         PELLET, mm("59.5"), ("touch", mm("5.0"))),
    Face("issf-p25", "25 m Precision / 50 m Pistol (ISSF)", "ISSF 6.3.4.5",
         _rings("mm", "50", "100", "150", "200", "250", "300", "350", "400", "450", "500"),
         RIMFIRE_ISSF, mm("200"), ("touch", mm("25")), note="500 mm across: larger than any FeT target box"),
    Face("issf-r50", "50 m Rifle (ISSF, NRA A-50)", "ISSF 6.3.4.2; NRA Smallbore 4.10 (A-50)",
         _rings("mm", "10.4", "26.4", "42.4", "58.4", "74.4", "90.4", "106.4", "122.4", "138.4", "154.4"),
         RIMFIRE_ISSF, mm("112.4"), ("touch", mm("5.0")), bull=5),
    Face("nra-a51", "50 yd: 50 m target reduced (NRA A-51)", "NRA Smallbore 4.8 (A-51)",
         _rings("mm", "9.034", "23.664", "38.295", "52.925", "67.556", "82.186", "96.816", "111.447", "126.077", "140.708"),
         RIMFIRE_NRA, mm("102.78"), ("touch", mm("4.096")), bull=5),
    Face("nra-usas50", "50 ft: 50 m target reduced (NRA/USAS-50)", "NRA Smallbore 4.3 (NRA/USAS-50)",
         _rings("mm", "0.76", "4.12", "9.00", "13.87", "18.75", "23.63", "28.50", "33.38"),
         RIMFIRE_NRA, mm("33.38"), None, note="no center shots on this target (14.3 f)", bull=1),
    Face("nra-a17", "50 ft conventional (NRA A-17)", "NRA Smallbore 4.2 (A-17); center shots 14.3 f 1",
         _rings("in", "0.150", "0.483", "0.817", "1.150", "1.483", "1.817"),
         RIMFIRE_NRA, inch("1.483"), ("a17", inch("0.150")), decimal=False, unit="in", bull=1, higher="covers"),
    Face("nra-a23", "50 yd conventional (NRA A-23)", "NRA Smallbore 4.6 (A-23/3, A-23/5, A-23/6); X-ring 14.3 c",
         _rings("in", "0.89", "1.89", "2.89", "3.89", "4.89", "5.89"),
         RIMFIRE_NRA, inch("3.89"), ("touch", inch("0.39")), decimal=False, unit="in", bull=5, higher="inside"),
    Face("nra-a27", "50 yd: 50 m target reduced, conventional prone (NRA A-27)", "NRA Smallbore 4.7 (A-27/5, A-27/6); X-ring 14.3 c",
         _rings("in", "0.719", "1.439", "2.159", "2.879", "3.599", "4.319", "5.038"),
         RIMFIRE_NRA, inch("3.89"), ("touch", inch("0.359")), decimal=False, unit="in", bull=5, higher="inside"),
    Face("nra-a26", "50 m conventional prone (NRA A-26)", "NRA Smallbore 4.9 (A-26/5, A-26/6); X-ring 14.3 c",
         _rings("in", "0.787", "1.574", "2.361", "3.148", "3.936", "4.723", "5.510"),
         RIMFIRE_NRA, inch("4.27"), ("touch", inch("0.393")), decimal=False, unit="in", bull=5, higher="inside"),
)}
DEFAULT_FACE = "issf-ar10"


def face(face_id: Optional[str]) -> Face:
    """The face with this id; the 10 m air rifle face for none or an unknown one (shots before faces existed)."""
    return FACES.get(face_id or DEFAULT_FACE, FACES[DEFAULT_FACE])


def view(f: Face) -> dict:
    """What the screens draw: ring diameters and the black in millimetres."""
    return {"id": f.id, "name": f.name, "rule": f.rule, "unit": f.unit, "decimal": f.decimal,
            "rings": [{"value": v, "dia_mm": d / TU_PER_MM} for v, d in f.rings],
            "black_mm": f.black / TU_PER_MM, "white_ten": f.white_ten,
            "inner_mm": f.inner[1] / TU_PER_MM if f.inner and f.inner[0] != "cover" else None,
            "projectile_mm": f.projectile / TU_PER_MM, "ring_mm": f.ring_step / TU_PER_MM, "note": f.note}
