"""Write tests/fixtures/fmath_golden.txt: inputs chosen to reach every branch of fetcore/fmath.py, with the exact bits
it returns. tests/test_fmath.py checks CPython against it, tests/test_micropython.py checks MicroPython. Regenerate
only after a deliberate change to fmath (it changes shot positions by an ulp: re-solve stored shots first).
    python3 tools/fmath_golden.py
"""
import math
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fetcore import fmath as F  # noqa: E402


def h(v):
    return struct.pack("<d", v).hex()


def inputs(rng):
    pio2 = math.pi / 2
    near = [n * pio2 + d for n in range(1, 40) for d in (0.0, 1e-9, -1e-12, 5e-15)]       # cancellation in rem_pio2
    near += [6381956970095103 * 2.0 ** -51 * k for k in (1, 3, 7)] + [5.0e5, 8.0e5, -7.3e5]
    one = {
        "exp": [rng.uniform(-745, 709) for _ in range(400)] + [rng.uniform(-1.1, 1.1) for _ in range(400)]
               + [rng.uniform(-1e-8, 1e-8) for _ in range(50)] + [2.0 ** -30, -2.0 ** -40, 0.0, 0.34657359, 1.03972077, -708.5, -740.0, 709.7],
        "log": [10 ** rng.uniform(-307, 308) for _ in range(400)] + [rng.uniform(0.6, 1.5) for _ in range(400)]
               + [1.0 + rng.uniform(-1e-6, 1e-6) for _ in range(50)] + [5e-324, 1e-310, 1.0, 2.0, 0.5, 273.15, 303.15],
        "sin": [rng.uniform(-7, 7) for _ in range(500)] + [rng.uniform(-1e5, 1e5) for _ in range(200)] + near
               + [1e-10, -3e-9, 0.785, 2.35, 1.5707963267948966, -1.5707963267948966, math.pi],
        "atan": [rng.uniform(-3, 3) for _ in range(500)] + [10 ** rng.uniform(-12, 25) for _ in range(150)]
                + [0.4, 0.44, 0.68, 0.7, 1.18, 1.2, 2.43, 2.45, 1e-12, 1e30],
        "acos": [rng.uniform(-1, 1) for _ in range(600)] + [rng.uniform(-1e-3, 1e-3) for _ in range(30)]
                + [0.5, -0.5, 0.4999999999999999, 1.0, -1.0, 1e-20, 0.99999999, -0.99999999],
    }
    # inputs that expose a one-ulp change of a constant (found by search, 2026-11-13: the mutation check)
    one["exp"] += [0.373446760712977, -712.9685592762254]
    one["sin"] += [5.6244828853976685, -0.7926628537507323, 80054.81436559337, -1.7210481343229986]
    one["atan"] += [0.3591834295038699, 0.3946412909907943]
    one["acos"] += [0.4647140304247671, 0.803739156132055]
    # branches the random values miss (operator mutation check, 2026-11-13):
    one["log"] += [2.0 ** k * (1 + d) for k in (-60, -3, -1, 1, 2, 10, 300) for d in (1e-7, -3e-8, 2.0 ** -52, -2.0 ** -53)]
    one["exp"] += [-707.3, -707.7, -707.9, -708.2, -1021 * math.log(2) + 0.2, -1021 * math.log(2) - 0.2]
    one["sin"] += [642615.9188844458, 321307.9594422229, 413441.44719405076, 505574.93494587863,    # 3rd pass
                   62.831854944331006, 157.0796326840613, 1570.796326793468, 52359.35396257814,     # 2nd pass
                   628320.101532572, 10.995574615257926, 31.41592653586936, -628320.101532572,
                   1.5707963, 1.5707962, -1.5707963, 1.57079632, -1.570796326]                       # high word 0x3FF921FB
    one["cos"] = one["sin"]
    two = {
        "atan2": [(rng.uniform(-200, 200), rng.uniform(-200, 200)) for _ in range(800)]
                 + [(10 ** rng.uniform(-30, 30), -10 ** rng.uniform(-30, 30)) for _ in range(100)]
                 + [(-0.4269597456416357, -1.2677871729585892), (1.0, 1.0), (-2.0, 1.0), (1e30, 1e-40), (1e-40, -1e30), (-1e-40, -1e30), (0.0, -1.0), (-0.0, -1.0), (3.0, 0.0)],
        "hypot": [(rng.uniform(-300, 300), rng.uniform(-300, 300)) for _ in range(800)]
                 + [(10 ** rng.uniform(-320, 308), 10 ** rng.uniform(-320, 308)) for _ in range(150)]
                 + [(2.0, 1.0), (1.0, 2.0), (1.0000000000000002, 1.0), (1.0, 1.0000000000000002), (1.0, 2.0 ** -60),
                    (3.0, 4.0), (1e300, 1e300), (1e-310, 3e-320), (1.0, 1e-20), (0.0, 0.0), (2.0, 1.9999999)],
    }
    return one, two


def main():
    rng = random.Random(20261113)
    one, two = inputs(rng)
    out = []
    for name, xs in one.items():
        f = getattr(F, name)
        for x in xs:
            out.append("%s %s %s" % (name, h(x), h(f(x))))
    for name, xs in two.items():
        f = getattr(F, name)
        for x, y in xs:
            out.append("%s %s %s %s" % (name, h(x), h(y), h(f(x, y))))
    path = os.path.join(ROOT, "tests", "fixtures", "fmath_golden.txt")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    print(len(out), "values ->", path)


if __name__ == "__main__":
    main()
