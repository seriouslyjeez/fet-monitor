"""Runs under CPython AND MicroPython (tests/test_micropython.py): both must print exactly the same text. It covers
what makes a shot's position and score: fetcore's fmath (the recorded bits), exact (numbers from text), lineparse
(Mega lines), targetproto (FeT target lines), shotsolve (the solver) and faces (scores on every face).
Floats are printed as their bits (MicroPython prints floats differently); numbers come from our own generator (the
two random modules differ). Usage: <python> tests/mp_conformance.py <repo root>"""
import struct
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, ROOT)
sys.path.insert(1, ROOT + "/firmware/display/lib")       # MicroPython only: __future__, typing, hmac stand-ins

from fetcore import exact, faces, fmath, lineparse, shotsolve, targetproto  # noqa: E402

if 1.0 + 2.0 ** -52 == 1.0:
    raise SystemExit("single-precision floats: build MicroPython with MICROPY_FLOAT_IMPL_DOUBLE")


def h(v):
    return struct.pack("<d", v).hex()


def fmt(v):
    if isinstance(v, float):
        return "f" + h(v)
    if isinstance(v, dict):
        return "{" + ",".join(fmt(k) + ":" + fmt(v[k]) for k in sorted(v)) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(fmt(x) for x in v) + "]"
    if isinstance(v, exact.Dec):
        return "D" + v.text
    return repr(v)


class Gen:
    """A 64-bit linear congruential generator; uniform() = 53 bits / 2**53 (exact on both)."""
    def __init__(self, seed):
        self.x = seed

    def next(self):
        self.x = (self.x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return self.x

    def uniform(self, a, b):
        return a + (b - a) * ((self.next() >> 11) / 9007199254740992.0)

    def choice(self, seq):
        return seq[self.next() % len(seq)]


def section(name):
    print("== " + name)


# ---------------------------------------------------------------------------------------- fmath: recorded bits
section("fmath golden")
bad = n = 0
with open(ROOT + "/tests/fixtures/fmath_golden.txt") as fh:
    for line in fh:
        p = line.split()
        args = [struct.unpack("<d", bytes.fromhex(v))[0] for v in p[1:-1]]
        n += 1
        if h(getattr(fmath, p[0])(*args)) != p[-1]:
            bad += 1
            if bad <= 5:
                print("MISMATCH", line.strip())
print("values", n, "mismatches", bad)

# ------------------------------------------------------------------------------------------- exact: from text
section("exact")
g = Gen(1)
texts = ["0.1", "-16.99", "31.48", "3.615", "2.675", "-0.005", "1.2345678901234567", "9007199254740993", "1e22",
         "1.5e-7", "642615.9188844458", "4.1e394", "2e-310", "0", "-0.0", "123456.789"]
for _ in range(300):
    m = g.next() % 10 ** (1 + g.next() % 16)
    point = g.next() % 9
    s = str(m)
    texts.append(("-" if g.next() % 2 else "") + (s[:-point] + "." + s[-point:] if 0 < point < len(s) else s))
for t in texts:
    try:
        hh = exact.to_hundredths(t)
    except ValueError:
        hh = "ValueError"
    print(t, fmt(exact.to_float(t)), hh)
print(fmt(exact.loads('{"a":[1,-2.50,3e2,null,true],"s":"x\\u00e9\\n","b":{"c":-0.0}}')))

# ------------------------------------------------------------------------------------ lineparse: Mega lines
section("lineparse")
base = ['{"shot":1, "miss":0, "name":"TARGET", "time":1.52 ,"x":1.20, "y":-0.80 , "N":4100, "E":4180, "S":4090, "W":4160 , "V_REF":4.94, , "T":19.00, , "VERSION":"4.3.0 December 21, 2023" }',
        '{"shot":7, "miss":1, "name":"T", "N":0, "E":0, "S":0, "W":0, "T":21.5}',
        '{"shot":3, "miss":0, "x":NAN, "y":nan, "N":1,"E":2,"S":3,"W":4}', '{"shot":3, "x":-12.345, "y":0.005, "face":2}',
        '{"shot":2.7, "miss":0.9, "x":"1.5", "y":1e2, "N":-3.9}']
pieces = [",", " ", "nan", "NaN", "INF", "-inf", "1.5", "1e3", "-0.005", '"', ":", "}", "{", '"x":', '"y":', '"miss":',
          "1", "0", "2.5", "e4", "e-4", "Infinity", "\\u0041", "7"]
g = Gen(2)
for _ in range(1500):
    t = g.choice(base)
    for _ in range(g.next() % 4):
        j = g.next() % (len(t) + 1)
        t = t[:j] + g.choice(pieces) + t[j:]
    try:
        r = lineparse.parse_shot_line(t)
        out = fmt([getattr(r, k) for k in lineparse.ShotLine.FIELDS])
    except ValueError as exc:
        out = "error " + type(exc).__name__
    print(out)
print(fmt(lineparse.parse_echo('{"SENSOR":230.00, "ANGLE":45, "NAME":"x", "Z_OFFSET":13.5, "A":NAN}')))

# ------------------------------------------------------------------------- targetproto: FeT target lines
section("targetproto")
key = bytes(range(32))


def signed(head):
    return head + " h=" + targetproto.mac(key, head.encode())


base = [signed("FT1 S b=12 q=3 t=4567 n=100 e=200 s=300 w=400 m=f f=- c=215 k=0a1b2c3d d=0"),
        signed("FT1 S b=12 q=3 t=4567 n=100 e=200 s=300 w=0 m=7 f=12 c=- k=0a1b2c3d d=1 r=80000000"),
        "FT1 S b=1 q=1 t=1 n=1 e=1 s=1 w=1 m=f f=- c=- k=00000000 d=0 h=-",
        signed("FT1 R b=5 i=9 r=ok vref_mv=1250 k=deadbeef"), signed("FT1 R b=5 i=9 r=busy"),
        signed("FT1 H b=77 fw=0.4.2 rev=520 k=0a1b2c3d key=1a2b3c4d"), "FT1 H b=77 fw=0.4.2 rev=520 k=0a1b2c3d key=none h=-"]
pieces = [" ", "=", "-", "+", "_", "0", "9", "f", "F", "x", "h=", " h=", "q=", "r=", "none", "1_0", "ab", "0x"]
g = Gen(3)
for _ in range(800):
    t = g.choice(base)
    for _ in range(g.next() % 3):
        j = g.next() % (len(t) + 1)
        t = t[:j] + g.choice(pieces) + t[j:]
    for fn in (targetproto.parse_shot, targetproto.parse_reply, targetproto.parse_hello):
        try:
            out = fmt(fn(t, key))
        except ValueError as exc:
            out = "error " + type(exc).__name__
        print(out)
print(targetproto.key_fingerprint(key), targetproto.make_command(1, 2, "set", {"vref_mv": 5}, key).strip())

# ------------------------------------------------------------------------------------ shotsolve: the solver
section("shotsolve")
g = Gen(4)
for i in range(2000):
    clock = 8.0 if i % 2 else 80.0
    geo = shotsolve.Geometry.from_upstream({"SENSOR": g.choice([230, 232, 228.5]), "ANGLE": 45, "NORTH_X": g.uniform(-2, 2),
                                            "EAST_Y": g.uniform(-2, 2)}, clock_mhz=clock)
    r = g.choice([3.0, 10.0, 30.0, 60.0, 100.0]) * g.uniform(0, 1)
    a = g.uniform(0, 6.283185307179586)
    temp = g.uniform(5, 35)
    counts = [c + g.choice([0.0, 0.0, 1.0, -1.0]) for c in
              shotsolve.forward(r * fmath.cos(a), r * fmath.sin(a), geo, temp_c=temp, t0_ticks=g.uniform(1e4, 6e4))]
    if g.next() % 20 == 0:
        counts[g.next() % 4] = None
    s = shotsolve.solve(counts, geo, temp)
    print(fmt([s.ok, s.reason, s.x, s.y, s.residual_mm, s.iterations]))
    if i % 10 == 0 and None not in counts:
        print(fmt(shotsolve.upstream_solve(counts, geo, temp)))
print(fmt(shotsolve.Geometry.from_upstream({}, clock_mhz=10.0).at_rate(80000000).walk_ticks))

# ------------------------------------------------------------------------------------- faces: every score
section("faces")
g = Gen(5)
for fid in sorted(faces.FACES):
    f = faces.FACES[fid]
    reach = f.rings[-1][1] // 2 // faces.TU_PER_H + 300
    for _ in range(10):                                   # short lines: the CYD's heap is small
        rows = []
        for _ in range(30):
            x, y = int(g.uniform(-reach, reach)), int(g.uniform(-reach, reach))
            sc = faces.score(f, x, y)
            rows.append("%d,%d,%d,%d" % (sc.ring, sc.tenths, 1 if sc.inner else 0, 1 if faces.higher(f, x, y) else 0))
        print(fid, " ".join(rows))
print("done")
