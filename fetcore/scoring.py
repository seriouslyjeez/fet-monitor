"""ISSF 10 m air rifle scoring in exact integer arithmetic.

All geometry is in hundredths of a millimetre (the firmware reports x/y with two
decimals), and comparisons use squared radii, so there is no floating point at
the ring lines and the same input always gives the same score.

Rules implemented (see the project spec, section 7):
* Ring k (1..10) has outer diameter 0.5 mm + 5 mm * (10 - k): ten = 0.5 mm ... ring 1 = 45.5 mm.
* A shot scores the higher ring if the pellet edge touches the ring line, so the
  pellet-centre radius is compared with ring outer radius + pellet radius, inclusive.
* Decimal score: each ring band is divided into ten equal sub-bands, 0.9 at the
  inner edge to 0.0 at the outer edge; the ten's band runs from the centre out to
  its reach.  With a 4.5 mm pellet every sub-band is exactly 0.25 mm wide.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

SCORING_VERSION = 1
# Inner ten (X) for 10 m air rifle: a 4.5 mm pellet covers the whole 0.5 mm ten dot when its centre is within
# 2.00 mm, which is exactly the 10.2 band. One setting so it is easy to change.
INNER_TEN_MIN_TENTHS = 102
DEFAULT_PELLET_DIA_H = 450  # 4.5 mm in hundredths of a millimetre


@dataclass(frozen=True)
class Score:
    ring: int    # 0..10; 0 means off the scoring rings (a miss)
    tenths: int  # decimal score x 10, 0..109

    @property
    def decimal(self) -> float:
        return self.tenths / 10.0


MISS = Score(0, 0)


def to_hundredths(value) -> int:
    """Convert a millimetre value (Decimal, int, float or str) to integer hundredths."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def ring_outer_radius_h(ring: int) -> int:
    """Outer radius of a ring in hundredths of a mm (ring 10 = 0.25 mm)."""
    if not 1 <= ring <= 10:
        raise ValueError("ring must be 1..10")
    return 25 + 250 * (10 - ring)


def ring_reach_h(ring: int, pellet_dia_h: int = DEFAULT_PELLET_DIA_H) -> int:
    """Largest pellet-centre radius that still scores this ring (inclusive)."""
    return ring_outer_radius_h(ring) + pellet_dia_h // 2


def score_xy(x_h: int, y_h: int, pellet_dia_h: int = DEFAULT_PELLET_DIA_H) -> Score:
    """Score a shot at (x_h, y_h) hundredths of a mm from the centre."""
    if pellet_dia_h <= 0 or pellet_dia_h % 2:
        raise ValueError("pellet_dia_h must be a positive even integer")
    r2 = x_h * x_h + y_h * y_h
    for ring in range(10, 0, -1):               # innermost first
        reach = ring_reach_h(ring, pellet_dia_h)
        if r2 <= reach * reach:
            lower = ring_reach_h(ring + 1, pellet_dia_h) if ring < 10 else 0
            width = reach - lower
            # smallest j in 0..9 with r <= lower + (j+1)*width/10, compared exactly
            for j in range(10):
                limit = 10 * lower + (j + 1) * width
                if 100 * r2 <= limit * limit:
                    return Score(ring, ring * 10 + 9 - j)
    return MISS


def is_inner_ten(tenths: int) -> bool:
    return tenths >= INNER_TEN_MIN_TENTHS
