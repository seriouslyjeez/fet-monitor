"""Shot position from the four microphone arrival times: the scoring authority's solver.

A target's counters run from each microphone's trigger until the target reads them, so a larger count means the
sound arrived earlier. Model, in the sensor plane (mm, us):

    arrival_i = t0 + slant_i / c + walk(d_i)
    slant_i   = sqrt((x - xi)^2 + (y - yi)^2 + z^2)     z = paper-to-sensor-plane distance
    walk(d)   = k * (d / 100 mm)^2                       the threshold is crossed later for weaker (farther) sound

Unknowns x, y, t0. Four microphones give one equation more than needed: the misfit (residual) says how well the
four times agree with one point on the paper, which is how a face-plate hit or a bad microphone shows up. Three
microphones still give a position (no residual). Solved by Gauss-Newton (Levenberg damped) from a closed-form start
(differences of squared distances are linear in x, y, t0), so there is no NaN and no silent non-convergence: every
result says why it failed.

`upstream_solve` is a faithful port of freETarget 4.3.x compute_hit()/send_score() (Arduino; double precision):
the yardstick, and the way to read shots FeT Monitor has stored from upstream firmware (`timers_from_reported`).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import fmath

N, E, S, W = 0, 1, 2, 3
NAMES = "NESW"


def _sq(v: float) -> float:
    return v * v                     # not v ** 2: float ** goes to the C library's pow, which differs by machine


def speed_of_sound(temp_c: float, rh: float = 50.0) -> float:
    """mm per us at temp_c and relative humidity rh (%); the same formula as freETarget (speed_of_sound.ino)."""
    r, m, c0 = 8314.46261815324, 28.966, 331.38
    if temp_c < 0:
        rh = 0
    tk = 273.15 + temp_c
    tk2 = tk * tk
    vp = fmath.exp((-7511.52 / tk) + 96.5389644 + 0.02399897 * tk - 0.000011654551 * tk2
                   - 0.000000012810336 * (tk2 * tk) + 0.000000000020998405 * (tk2 * tk2) - 12.150799 * fmath.log(tk))
    mf = 0.01 * rh * vp / 101325.0
    shr = (7.0 + mf) / (5.0 + mf)
    mmw = m - (m - 18.01528) * mf
    change = (1.0 / fmath.sqrt(1.4 / m) * 100.0) * fmath.sqrt(shr / mmw) - 100.0
    gamma = 1.40092 - 0.0000196395 * temp_c - 0.000000162593 * temp_c * temp_c
    return (fmath.sqrt(gamma * r * tk / m) + (c0 / 100.0) * change) / 1000.0


class Geometry:
    """Where the microphones are (sensor frame, mm: N on +y, E on +x) and how the target turns that into the target
    frame (angle_deg, counter-clockwise). clock_mhz: counter clock (Mega 8). walk_ticks: threshold delay at 100 mm
    in counter ticks (freETarget's DOPPLER). A plain class (no dataclass): fetcore also runs on MicroPython."""
    FIELDS = ("sensors", "z_mm", "angle_deg", "clock_mhz", "walk_ticks")

    def __init__(self, sensors: List[Tuple[float, float]], z_mm: float = 13.0, angle_deg: float = 45.0,
                 clock_mhz: float = 8.0, walk_ticks: float = 50.0):
        self.sensors = sensors
        self.z_mm = z_mm
        self.angle_deg = angle_deg
        self.clock_mhz = clock_mhz
        self.walk_ticks = walk_ticks

    def __eq__(self, other):
        return isinstance(other, Geometry) and all(getattr(self, k) == getattr(other, k) for k in self.FIELDS)

    def __repr__(self):
        return "Geometry(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.FIELDS)

    def replace(self, **changes) -> "Geometry":
        """A copy with some fields changed (dataclasses.replace)."""
        kw = {k: getattr(self, k) for k in self.FIELDS}
        for k, v in changes.items():
            if k not in kw:
                raise TypeError("Geometry has no field %r" % k)
            kw[k] = v
        return Geometry(**kw)

    def at_rate(self, rate_hz: Optional[int]) -> "Geometry":
        """The geometry for counts at rate_hz (a shot line's r=): that clock, and the threshold delay walk_ticks kept
        the same in microseconds. None: unchanged (the board revision's clock)."""
        if rate_hz is None:
            return self
        mhz = rate_hz / 1e6
        return self.replace(clock_mhz=mhz, walk_ticks=self.walk_ticks * mhz / self.clock_mhz)

    @classmethod
    def from_upstream(cls, settings: Optional[dict] = None, clock_mhz: float = 8.0) -> "Geometry":
        """From freETarget settings (ECHO names): SENSOR diameter, the eight NORTH_X ... WEST_Y offsets (positive =
        outward on the radial axis, as upstream applies them), Z_OFFSET, ANGLE, DOPPLER. Missing = upstream default."""
        s = dict(settings or {})
        r = float(s.get("SENSOR", 230.0)) / 2.0
        g = lambda k: float(s.get(k, 0) or 0)
        sensors = [(g("NORTH_X"), r + g("NORTH_Y")), (r + g("EAST_X"), g("EAST_Y")),
                   (g("SOUTH_X"), -(r + g("SOUTH_Y"))), (-(r + g("WEST_X")), g("WEST_Y"))]
        return cls(sensors=sensors, z_mm=float(s.get("Z_OFFSET", 13)), angle_deg=float(s.get("ANGLE", 45)),
                   clock_mhz=clock_mhz, walk_ticks=float(s.get("DOPPLER", 50)))

    def to_target(self, x: float, y: float) -> Tuple[float, float]:
        a = fmath.radians(self.angle_deg)
        return x * fmath.cos(a) - y * fmath.sin(a), x * fmath.sin(a) + y * fmath.cos(a)

    def to_sensor(self, x: float, y: float) -> Tuple[float, float]:
        a = fmath.radians(-self.angle_deg)
        return x * fmath.cos(a) - y * fmath.sin(a), x * fmath.sin(a) + y * fmath.cos(a)


class Solution:
    """ok; reason (why not ok); x, y (target frame, mm); sensor_xy; residual_us (the largest misfit of one microphone,
    None with only three microphones) and residual_mm (the same as a distance); used (microphones, e.g. "NESW");
    iterations; residuals."""
    FIELDS = ("ok", "reason", "x", "y", "sensor_xy", "residual_us", "residual_mm", "used", "iterations", "residuals")

    def __init__(self, ok: bool, reason: str = "", x: float = 0.0, y: float = 0.0,
                 sensor_xy: Tuple[float, float] = (0.0, 0.0), residual_us: Optional[float] = None,
                 residual_mm: Optional[float] = None, used: str = "", iterations: int = 0,
                 residuals: Optional[List[float]] = None):
        self.ok = ok
        self.reason = reason
        self.x = x
        self.y = y
        self.sensor_xy = sensor_xy
        self.residual_us = residual_us
        self.residual_mm = residual_mm
        self.used = used
        self.iterations = iterations
        self.residuals = [] if residuals is None else residuals

    def __eq__(self, other):
        return isinstance(other, Solution) and all(getattr(self, k) == getattr(other, k) for k in self.FIELDS)

    def __repr__(self):
        return "Solution(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.FIELDS)

    @property
    def radius(self) -> float:
        return fmath.hypot(self.x, self.y)


def forward(x: float, y: float, geom: Geometry, temp_c: float = 20.0, rh: float = 50.0,
            t0_ticks: float = 20000.0, quantise: bool = True) -> List[float]:
    """Counter values for a shot at (x, y) in the SENSOR frame under this model (tests, simulation, calibration)."""
    c = speed_of_sound(temp_c, rh)
    k_us = geom.walk_ticks / geom.clock_mhz
    out = []
    for (sx, sy) in geom.sensors:
        d = fmath.hypot(x - sx, y - sy)
        arrival_us = fmath.sqrt(d * d + geom.z_mm * geom.z_mm) / c + k_us * (d / 100.0) * (d / 100.0)
        v = t0_ticks - arrival_us * geom.clock_mhz
        out.append(float(round(v)) if quantise else v)
    return out


def _solve3(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """3x3 linear solve (Gaussian elimination, partial pivoting); None if singular."""
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(3):
            if r != col:
                f = m[r][col] / m[col][col]
                for k in range(col, 4):
                    m[r][k] -= f * m[col][k]
    return [m[i][3] / m[i][i] for i in range(3)]


def solve(timers: Sequence[Optional[float]], geom: Geometry, temp_c: float = 20.0, rh: float = 50.0,
          max_iter: int = 50) -> Solution:
    """Position from counter values (None = that microphone did not trigger)."""
    idx = [i for i in range(4) if timers[i] is not None]
    used = "".join(NAMES[i] for i in idx)
    if len(idx) < 3:
        return Solution(False, f"only {len(idx)} microphones triggered", used=used)
    for i in idx:
        if not fmath.isfinite(timers[i]):
            return Solution(False, "a counter value is not a number", used=used)
    c = speed_of_sound(temp_c, rh)
    k = geom.walk_ticks / geom.clock_mhz                                 # us at 100 mm
    p = [geom.sensors[i] for i in idx]
    tau = [-float(timers[i]) / geom.clock_mhz for i in idx]             # arrival times, us (up to a constant)
    base = max(tau)
    tau = [t - base for t in tau]                                        # keep the numbers small

    # closed-form start: (x-xi)^2+(y-yi)^2 + z^2 = c^2 (tau_i - t0)^2, minus the same for sensor 0, is linear
    rows, rhs = [], []
    x0, y0 = p[0]
    for j in range(1, len(idx)):
        xj, yj = p[j]
        rows.append([-2 * (xj - x0), -2 * (yj - y0), 2 * c * c * (tau[j] - tau[0])])
        rhs.append(c * c * (tau[j] * tau[j] - tau[0] * tau[0]) - (xj * xj + yj * yj - x0 * x0 - y0 * y0))
    if len(rows) == 3:
        start = _solve3(rows, rhs)
    else:                                                                # three microphones: least squares in x, y
        start = None
    if start is None:                                                    # with three, or degenerate: from the centre
        start = [0.0, 0.0, min(t - fmath.hypot(px, py) / c for t, (px, py) in zip(tau, p))]
    x, y, t0 = start
    if not all(map(fmath.isfinite, (x, y, t0))) or fmath.hypot(x, y) > 10 * max(fmath.hypot(*s) for s in p):
        x, y, t0 = 0.0, 0.0, min(t - fmath.hypot(px, py) / c for t, (px, py) in zip(tau, p))

    lam = 1e-6
    it = 0

    def model(x, y, t0):
        res, jac = [], []
        for (px, py), t in zip(p, tau):
            dx, dy = x - px, y - py
            d = fmath.hypot(dx, dy)
            sl = fmath.sqrt(d * d + geom.z_mm * geom.z_mm)
            res.append(t0 + sl / c + k * (d / 100.0) * (d / 100.0) - t)
            jac.append([dx / (c * sl) + 2 * k * dx / 1e4, dy / (c * sl) + 2 * k * dy / 1e4, 1.0])
        return res, jac

    res, jac = model(x, y, t0)
    cost = sum(r * r for r in res)
    for it in range(1, max_iter + 1):
        jtj = [[sum(jac[n][a] * jac[n][b] for n in range(len(res))) for b in range(3)] for a in range(3)]
        jtr = [sum(jac[n][a] * res[n] for n in range(len(res))) for a in range(3)]
        for _ in range(40):                                              # raise the damping until a step helps
            damped = [[jtj[a][b] * (1 + lam if a == b else 1) for b in range(3)] for a in range(3)]
            step = _solve3(damped, [-v for v in jtr])
            if step is None:
                return Solution(False, "geometry is degenerate", used=used, iterations=it)
            nx, ny, nt = x + step[0], y + step[1], t0 + step[2]
            nres, njac = model(nx, ny, nt)
            ncost = sum(r * r for r in nres)
            if ncost <= cost or lam > 1e6:
                break
            lam *= 10
        x, y, t0, res, jac, cost = nx, ny, nt, nres, njac, ncost
        lam = max(lam / 10, 1e-9)
        if fmath.hypot(step[0], step[1]) < 1e-7:
            break
    else:
        return Solution(False, "did not converge", used=used, iterations=it)
    if not all(map(fmath.isfinite, (x, y))):
        return Solution(False, "did not converge", used=used, iterations=it)
    tx, ty = geom.to_target(x, y)
    worst = max(abs(r) for r in res) if len(idx) == 4 else None
    return Solution(True, "", tx, ty, (x, y), worst, None if worst is None else worst * c, used, it, res)


# ------------------------------------------------------------------------------------------- freETarget 4.3.x
def upstream_solve(timers: Sequence[float], geom: Geometry, temp_c: float = 20.0, rh: float = 50.0) -> dict:
    """compute_hit() + send_score() of freETarget 4.3.x (Arduino), in double precision. Keeps its behaviour:
    miss if any counter is 0, the plain 4-way average, NaN when the 3-D correction goes complex, the 20-round cap.
    Returns x/y (target frame), sensor_xy, counts (what it prints as N/E/S/W), miss, iterations."""
    if any(t == 0 for t in timers):
        return {"miss": True, "reason": "a counter is 0"}
    c = speed_of_sound(temp_c, rh)
    osc = geom.clock_mhz
    sx = [px / c * osc for px, _ in geom.sensors]
    sy = [py / c * osc for _, py in geom.sensors]
    zc = geom.z_mm * osc / c
    ctm = c / osc
    dia = 2 * fmath.hypot(*geom.sensors[N])                     # SENSOR (nominal ring); offsets are small
    shx = shy = 0.0
    est = dia / 2 * osc
    err, n = 1e9, 0
    cnt = list(timers)
    while err > 0.001:
        dop = [int(geom.walk_ticks * _sq(fmath.hypot(px - shx, py - shy) / 100.0) + 0.5) for px, py in geom.sensors]
        cnt = [timers[i] + dop[i] for i in range(4)]
        largest = max(cnt)
        trig = cnt.index(largest)
        cnt = [largest - v for v in cnt]
        b = cnt[:]
        cc = [fmath.hypot(sx[i] - sx[(i + 1) % 4], sy[i] - sy[(i + 1) % 4]) for i in range(4)]
        a = [b[(i + 1) % 4] for i in range(4)]
        last = est
        xa = ya = 0.0
        for i in range(4):
            va = _sq(a[i] + est) - zc * zc
            vb = _sq(b[i] + est) - zc * zc
            ae = fmath.sqrt(va) if va >= 0 else fmath.nan       # upstream: sqrt of a negative, its "fix" is a no-op
            be = fmath.sqrt(vb) if vb >= 0 else fmath.nan
            if ae + be < cc[i]:
                ang = 0.0
            else:
                q = (ae * ae - be * be - cc[i] * cc[i]) / (-2 * be * cc[i])
                ang = fmath.acos(q) if -1 <= q <= 1 else fmath.nan
            if i == N:
                r = fmath.pi / 4 - ang
                xr, yr = sx[i] + be * fmath.sin(r), sy[i] - be * fmath.cos(r)
            elif i == E:
                r = ang - fmath.pi / 4
                xr, yr = sx[i] - be * fmath.cos(r), sy[i] + be * fmath.sin(r)
            elif i == S:
                r = ang + fmath.pi / 4
                xr, yr = sx[i] - be * fmath.cos(r), sy[i] + be * fmath.sin(r)
            else:
                r = fmath.pi / 4 - ang
                xr, yr = sx[i] + be * fmath.cos(r), sy[i] + be * fmath.sin(r)
            xa += xr
            ya += yr
        xa /= 4
        ya /= 4
        shx, shy = xa * ctm, ya * ctm
        est = fmath.hypot(sx[trig] - xa, sy[trig] - ya)
        err = abs(last - est)
        n += 1
        if n > 20:
            break                                               # (a NaN error also ends the loop, as in C)
    rad = fmath.hypot(shx, shy)
    if rad > dia / 2:
        return {"miss": True, "reason": "outside the sensor circle", "sensor_xy": (shx, shy), "iterations": n}
    angle = fmath.degrees(fmath.atan2(shy, shx)) + geom.angle_deg
    return {"miss": False, "x": rad * fmath.cos(fmath.radians(angle)), "y": rad * fmath.sin(fmath.radians(angle)),
            "sensor_xy": (shx, shy), "counts": [int(v) for v in cnt], "iterations": n}


def timers_from_reported(counts: Sequence[int], x: float, y: float, geom: Geometry, base: float = 20000.0) -> List[float]:
    """Counter values that make upstream print these N/E/S/W counts for a shot it reported at (x, y): the counts it
    prints are `largest - (counter + walk)`, with the walk taken at the final position. For re-solving stored shots."""
    px, py = geom.to_sensor(x, y)
    dop = [int(geom.walk_ticks * _sq(fmath.hypot(sx - px, sy - py) / 100.0) + 0.5) for sx, sy in geom.sensors]
    return [base - counts[i] - dop[i] for i in range(4)]
