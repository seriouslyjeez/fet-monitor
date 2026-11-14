"""The elementary functions fetcore uses, giving the same bits on every machine.

The C library behind `math` differs between machines: the Pi, deepTENS and the ESP32 (newlib) round exp, log, sin,
cos, atan2, acos and pow differently in the last bit for a few percent of inputs (measured 2026-11-13 on a CYD). A
shot position computed with them can then differ by one unit in the last place, and in principle land on the other
side of a hundredth of a millimetre. These are fdlibm's algorithms (Sun Microsystems, 1993: the basis of most C
libraries), written with only + - * / and sqrt, which IEEE 754 makes exact (correctly rounded) everywhere, so the
results are the same bits on CPython and MicroPython, on any processor. Errors are below one unit in the last place.

Only what fetcore needs: arguments of sin and cos up to about 8e5 (no shot geometry comes near), and errors raised
like `math` does (ValueError) where fetcore relies on them.

Freely usable: fdlibm's notice is "Copyright (C) 1993 by Sun Microsystems, Inc. All rights reserved. Developed at
SunSoft, a Sun Microsystems, Inc. business. Permission to use, copy, modify, and distribute this software is freely
granted, provided that this notice is preserved." (NOTICE)
"""
import math
import struct


def _d(bits):
    """A double from its 64 bits: fdlibm's constants are given this way, so no decimal parser is involved (MicroPython
    reads some long decimals one unit in the last place off: 5 of fdlibm's, 2026-11-13)."""
    return struct.unpack("<d", struct.pack("<Q", bits))[0]

pi = _d(0x400921FB54442D18)    # 3.14159265358979311600e+00
inf = float("inf")
nan = float("nan")
sqrt = math.sqrt            # correctly rounded by IEEE 754 on every platform
isfinite = math.isfinite
fabs = abs

_DEG = pi / 180.0
_RAD = 180.0 / pi


def radians(x):
    return x * _DEG


def degrees(x):
    return x * _RAD


# ------------------------------------------------------------------------------------- the words of a double
def _bits(x):
    return struct.unpack("<q", struct.pack("<d", x))[0]


def _hi(x):
    return _bits(x) >> 32                       # signed, as fdlibm's int32 high word


def _lo(x):
    return _bits(x) & 0xFFFFFFFF


def _make(hi, lo):
    return struct.unpack("<d", struct.pack("<Q", ((hi & 0xFFFFFFFF) << 32) | (lo & 0xFFFFFFFF)))[0]


def _set_hi(x, hi):
    return _make(hi, _lo(x))


# --------------------------------------------------------------------------------------------- hypot (e_hypot.c)
def hypot(x, y):
    """sqrt(x*x + y*y) without undue overflow and under 1 ulp (math.hypot differs between Python versions and is
    missing on MicroPython; a plain sqrt(x*x + y*y) is up to 2 ulps off)."""
    ha = _hi(x) & 0x7FFFFFFF
    hb = _hi(y) & 0x7FFFFFFF
    if hb > ha:
        a, b = y, x
        ha, hb = hb, ha
    else:
        a, b = x, y
    a = _set_hi(a, ha)                           # |a| >= |b|
    b = _set_hi(b, hb)
    if ha - hb > 0x3C00000:                      # a/b > 2**60
        return a + b
    k = 0
    if ha > 0x5F300000:                          # a > 2**500
        if ha >= 0x7FF00000:                     # inf or nan
            w = a + b
            if ((ha & 0xFFFFF) | _lo(a)) == 0:
                w = a
            if ((hb ^ 0x7FF00000) | _lo(b)) == 0:
                w = b
            return w
        ha -= 0x25800000                         # scale by 2**-600
        hb -= 0x25800000
        k += 600
        a = _set_hi(a, ha)
        b = _set_hi(b, hb)
    if hb < 0x20B00000:                          # b < 2**-500
        if hb <= 0x000FFFFF:                     # subnormal b or 0
            if (hb | _lo(b)) == 0:
                return a
            t1 = _make(0x7FD00000, 0)            # 2**1022
            b *= t1
            a *= t1
            k -= 1022
            ha = _hi(a)                          # fdlibm 5.3 keeps the old words here (FreeBSD msun's fix)
            hb = _hi(b)
        else:                                    # scale by 2**600
            ha += 0x25800000
            hb += 0x25800000
            k -= 600
            a = _set_hi(a, ha)
            b = _set_hi(b, hb)
    w = a - b
    if w > b:
        t1 = _make(ha, 0)
        t2 = a - t1
        w = sqrt(t1 * t1 - (b * (-b) - t2 * (a + t1)))
    else:
        a = a + a
        y1 = _make(hb, 0)
        y2 = b - y1
        t1 = _make(ha + 0x00100000, 0)
        t2 = a - t1
        w = sqrt(t1 * y1 - (w * (-w) - (t1 * y2 + t2 * b)))
    if k != 0:
        return _make(0x3FF00000 + (k << 20), 0) * w
    return w


# ------------------------------------------------------------------------------------------------- exp (e_exp.c)
_O_THRESHOLD = _d(0x40862E42FEFA39EF)    # 7.09782712893383973096e+02
_U_THRESHOLD = -_d(0x40874910D52D3051)   # -7.45133219101941108420e+02
_LN2HI = (_d(0x3FE62E42FEE00000), -_d(0x3FE62E42FEE00000))
_LN2LO = (_d(0x3DEA39EF35793C76), -_d(0x3DEA39EF35793C76))
_HALF = (0.5, -0.5)
_INVLN2 = _d(0x3FF71547652B82FE)    # 1.44269504088896338700e+00
_P1 = _d(0x3FC555555555553E)    # 1.66666666666666019037e-01
_P2 = -_d(0x3F66C16C16BEBD93)    # 2.77777777770155933842e-03
_P3 = _d(0x3F11566AAF25DE2C)    # 6.61375632143793436117e-05
_P4 = -_d(0x3EBBBD41C5D26BF1)    # 1.65339022054652515390e-06
_P5 = _d(0x3E66376972BEA4D0)    # 4.13813679705723846039e-08
_TWOM1000 = _d(0x0170000000000000)      # 2**-1000


def exp(x):
    hx = _hi(x)
    xsb = (hx >> 31) & 1
    hx &= 0x7FFFFFFF
    if hx >= 0x40862E42:                         # |x| >= 709.78...
        if hx >= 0x7FF00000:
            if ((hx & 0xFFFFF) | _lo(x)) != 0:
                return x + x                     # nan
            return 0.0 if xsb else x             # exp(+-inf) = inf, 0
        if x > _O_THRESHOLD:
            raise OverflowError("math range error")
        if x < _U_THRESHOLD:
            return 0.0
    k = 0
    hi = lo = 0.0
    if hx > 0x3FD62E42:                          # |x| > 0.5 ln2
        if hx < 0x3FF0A2B2:                      # and < 1.5 ln2
            hi = x - _LN2HI[xsb]
            lo = _LN2LO[xsb]
            k = 1 - xsb - xsb
        else:
            k = int(_INVLN2 * x + _HALF[xsb])
            t = float(k)
            hi = x - t * _LN2HI[0]
            lo = t * _LN2LO[0]
        x = hi - lo
    elif hx < 0x3E300000:                        # |x| < 2**-28
        return 1.0 + x
    t = x * x
    c = x - t * (_P1 + t * (_P2 + t * (_P3 + t * (_P4 + t * _P5))))
    if k == 0:
        return 1.0 - ((x * c) / (c - 2.0) - x)
    y = 1.0 - ((lo - (x * c) / (2.0 - c)) - hi)
    if k >= -1021:
        return _set_hi(y, _hi(y) + (k << 20))
    return _set_hi(y, _hi(y) + ((k + 1000) << 20)) * _TWOM1000


# ------------------------------------------------------------------------------------------------- log (e_log.c)
_LN2_HI = _d(0x3FE62E42FEE00000)    # 6.93147180369123816490e-01
_LN2_LO = _d(0x3DEA39EF35793C76)    # 1.90821492927058770002e-10
_TWO54 = _d(0x4350000000000000)    # 1.80143985094819840000e+16
_LG1 = _d(0x3FE5555555555593)    # 6.666666666666735130e-01
_LG2 = _d(0x3FD999999997FA04)    # 3.999999999940941908e-01
_LG3 = _d(0x3FD2492494229359)    # 2.857142874366239149e-01
_LG4 = _d(0x3FCC71C51D8E78AF)    # 2.222219843214978396e-01
_LG5 = _d(0x3FC7466496CB03DE)    # 1.818357216161805012e-01
_LG6 = _d(0x3FC39A09D078C69F)    # 1.531383769920937332e-01
_LG7 = _d(0x3FC2F112DF3E5244)    # 1.479819860511658591e-01


def log(x):
    if x != x:
        return x                                 # nan (of either sign), as math.log
    hx = _hi(x)
    lx = _lo(x)
    k = 0
    if hx < 0x00100000:                          # x < 2**-1022
        if ((hx & 0x7FFFFFFF) | lx) == 0 or hx < 0:
            raise ValueError("math domain error")    # log(0), log(x < 0)
        k -= 54
        x *= _TWO54
        hx = _hi(x)
    if hx >= 0x7FF00000:
        return x + x                             # inf, nan
    k += (hx >> 20) - 1023
    hx &= 0x000FFFFF
    i = (hx + 0x95F64) & 0x100000
    x = _set_hi(x, hx | (i ^ 0x3FF00000))        # x or x/2, in [sqrt(2)/2, sqrt(2)]
    k += i >> 20
    f = x - 1.0
    if (0x000FFFFF & (2 + hx)) < 3:              # -2**-20 <= f < 2**-20
        if f == 0.0:
            if k == 0:
                return 0.0
            dk = float(k)
            return dk * _LN2_HI + dk * _LN2_LO
        r = f * f * (0.5 - _d(0x3FD5555555555555) * f)    # 0.33333333333333333
        if k == 0:
            return f - r
        dk = float(k)
        return dk * _LN2_HI - ((r - dk * _LN2_LO) - f)
    s = f / (2.0 + f)
    dk = float(k)
    z = s * s
    i = hx - 0x6147A
    w = z * z
    j = 0x6B851 - hx
    t1 = w * (_LG2 + w * (_LG4 + w * _LG6))
    t2 = z * (_LG1 + w * (_LG3 + w * (_LG5 + w * _LG7)))
    i |= j
    r = t2 + t1
    if i > 0:
        hfsq = 0.5 * f * f
        if k == 0:
            return f - (hfsq - s * (hfsq + r))
        return dk * _LN2_HI - ((hfsq - (s * (hfsq + r) + dk * _LN2_LO)) - f)
    if k == 0:
        return f - s * (f - r)
    return dk * _LN2_HI - ((s * (f - r) - dk * _LN2_LO) - f)


# ------------------------------------------------------------------------- sin, cos (k_sin.c, k_cos.c, e_rem_pio2.c)
_S1 = -_d(0x3FC5555555555549)    # 1.66666666666666324348e-01
_S2 = _d(0x3F8111111110F8A6)    # 8.33333333332248946124e-03
_S3 = -_d(0x3F2A01A019C161D5)    # 1.98412698298579493134e-04
_S4 = _d(0x3EC71DE357B1FE7D)    # 2.75573137070700676789e-06
_S5 = -_d(0x3E5AE5E68A2B9CEB)    # 2.50507602534068634195e-08
_S6 = _d(0x3DE5D93A5ACFD57C)    # 1.58969099521155010221e-10
_C1 = _d(0x3FA555555555554C)    # 4.16666666666666019037e-02
_C2 = -_d(0x3F56C16C16C15177)    # 1.38888888888741095749e-03
_C3 = _d(0x3EFA01A019CB1590)    # 2.48015872894767294178e-05
_C4 = -_d(0x3E927E4F809C52AD)    # 2.75573143513906633035e-07
_C5 = _d(0x3E21EE9EBDB4B1C4)    # 2.08757232129817482790e-09
_C6 = -_d(0x3DA8FAE9BE8838D4)    # 1.13596475577881948265e-11


def _ksin(x, y, iy):
    """sin(x + y) for |x| <= pi/4, y the tail of x."""
    if (_hi(x) & 0x7FFFFFFF) < 0x3E400000 and int(x) == 0:     # |x| < 2**-27
        return x
    z = x * x
    v = z * x
    r = _S2 + z * (_S3 + z * (_S4 + z * (_S5 + z * _S6)))
    if iy == 0:
        return x + v * (_S1 + z * r)
    return x - ((z * (0.5 * y - v * r) - y) - v * _S1)


def _kcos(x, y):
    """cos(x + y) for |x| <= pi/4."""
    ix = _hi(x) & 0x7FFFFFFF
    if ix < 0x3E400000 and int(x) == 0:          # |x| < 2**-27
        return 1.0
    z = x * x
    r = z * (_C1 + z * (_C2 + z * (_C3 + z * (_C4 + z * (_C5 + z * _C6)))))
    if ix < 0x3FD33333:                          # |x| < 0.3
        return 1.0 - (0.5 * z - (z * r - x * y))
    qx = 0.28125 if ix > 0x3FE90000 else _make(ix - 0x00200000, 0)    # x/4
    hz = 0.5 * z - qx
    a = 1.0 - qx
    return a - (hz - (z * r - x * y))


_INVPIO2 = _d(0x3FE45F306DC9C883)    # 6.36619772367581382433e-01
_PIO2_1 = _d(0x3FF921FB54400000)    # 1.57079632673412561417e+00
_PIO2_1T = _d(0x3DD0B4611A626331)    # 6.07710050650619224932e-11
_PIO2_2 = _d(0x3DD0B4611A600000)    # 6.07710050630396597660e-11
_PIO2_2T = _d(0x3BA3198A2E037073)    # 2.02226624879595063154e-21
_PIO2_3 = _d(0x3BA3198A2E000000)    # 2.02226624871116645580e-21
_PIO2_3T = _d(0x397B839A252049C1)    # 8.47842766036889956997e-32


_NPIO2_HW = (0x3FF921FB, 0x400921FB, 0x4012D97C, 0x401921FB, 0x401F6A7A, 0x4022D97C,
             0x4025FDBB, 0x402921FB, 0x402C463A, 0x402F6A7A, 0x4031475C, 0x4032D97C,
             0x40346B9C, 0x4035FDBB, 0x40378FDB, 0x403921FB, 0x403AB41B, 0x403C463A,
             0x403DD85A, 0x403F6A7A, 0x40407E4C, 0x4041475C, 0x4042106C, 0x4042D97C,
             0x4043A28C, 0x40446B9C, 0x404534AC, 0x4045FDBB, 0x4046C6CB, 0x40478FDB,
             0x404858EB, 0x404921FB)


def _rem_pio2(x):
    """(n, y0, y1): x = n*pi/2 + y0 + y1, |y0 + y1| <= pi/4. Up to |x| ~ 2**19 * pi/2 (fdlibm's medium case; larger
    arguments need its __kernel_rem_pio2, which fetcore never does)."""
    hx = _hi(x)
    ix = hx & 0x7FFFFFFF
    if ix <= 0x3FE921FB:                         # |x| <= pi/4
        return 0, x, 0.0
    if ix < 0x4002D97C:                          # |x| < 3pi/4
        if hx > 0:
            z = x - _PIO2_1
            if ix != 0x3FF921FB:
                y0 = z - _PIO2_1T
                return 1, y0, (z - y0) - _PIO2_1T
            z -= _PIO2_2
            y0 = z - _PIO2_2T
            return 1, y0, (z - y0) - _PIO2_2T
        z = x + _PIO2_1
        if ix != 0x3FF921FB:
            y0 = z + _PIO2_1T
            return -1, y0, (z - y0) + _PIO2_1T
        z += _PIO2_2
        y0 = z + _PIO2_2T
        return -1, y0, (z - y0) + _PIO2_2T
    if ix > 0x413921FB:
        raise ValueError("fmath: sin/cos argument too large")
    t = abs(x)
    n = int(t * _INVPIO2 + 0.5)
    fn = float(n)
    r = t - fn * _PIO2_1
    w = fn * _PIO2_1T                            # 1st round: good to 85 bits
    y0 = r - w
    if not (n < 32 and ix != _NPIO2_HW[n - 1]):  # fdlibm's quick check: no cancellation
        j = ix >> 20
        i = j - ((_hi(y0) >> 20) & 0x7FF)
        if i > 16:                               # 2nd iteration: good to 118 bits
            t = r
            w = fn * _PIO2_2
            r = t - w
            w = fn * _PIO2_2T - ((t - r) - w)
            y0 = r - w
            i = j - ((_hi(y0) >> 20) & 0x7FF)
            if i > 49:                           # 3rd iteration: 151 bits
                t = r
                w = fn * _PIO2_3
                r = t - w
                w = fn * _PIO2_3T - ((t - r) - w)
                y0 = r - w
    y1 = (r - y0) - w
    if hx < 0:
        return -n, -y0, -y1
    return n, y0, y1


def sin(x):
    if x != x:
        return x                                 # nan passes through, as math.sin
    ix = _hi(x) & 0x7FFFFFFF
    if ix <= 0x3FE921FB:
        return _ksin(x, 0.0, 0)
    if ix >= 0x7FF00000:
        raise ValueError("math domain error")
    n, y0, y1 = _rem_pio2(x)
    n &= 3
    if n == 0:
        return _ksin(y0, y1, 1)
    if n == 1:
        return _kcos(y0, y1)
    if n == 2:
        return -_ksin(y0, y1, 1)
    return -_kcos(y0, y1)


def cos(x):
    if x != x:
        return x                                 # nan passes through, as math.cos
    ix = _hi(x) & 0x7FFFFFFF
    if ix <= 0x3FE921FB:
        return _kcos(x, 0.0)
    if ix >= 0x7FF00000:
        raise ValueError("math domain error")
    n, y0, y1 = _rem_pio2(x)
    n &= 3
    if n == 0:
        return _kcos(y0, y1)
    if n == 1:
        return -_ksin(y0, y1, 1)
    if n == 2:
        return -_kcos(y0, y1)
    return _ksin(y0, y1, 1)


# --------------------------------------------------------------------------------------- atan, atan2 (s_atan.c, e_atan2.c)
_ATANHI = (_d(0x3FDDAC670561BB4F), _d(0x3FE921FB54442D18), _d(0x3FEF730BD281F69B), _d(0x3FF921FB54442D18))
_ATANLO = (_d(0x3C7A2B7F222F65E2), _d(0x3C81A62633145C07), _d(0x3C7007887AF0CBBD), _d(0x3C91A62633145C07))
_AT = (_d(0x3FD555555555550D), -_d(0x3FC999999998EBC4), _d(0x3FC24924920083FF), -_d(0x3FBC71C6FE231671),
       _d(0x3FB745CDC54C206E), -_d(0x3FB3B0F2AF749A6D), _d(0x3FB10D66A0D03D51), -_d(0x3FADDE2D52DEFD9A),
       _d(0x3FA97B4B24760DEB), -_d(0x3FA2B4442C6A6C2F), _d(0x3F90AD3AE322DA11))


def atan(x):
    hx = _hi(x)
    ix = hx & 0x7FFFFFFF
    if ix >= 0x44100000:                         # |x| >= 2**66
        if ix > 0x7FF00000 or (ix == 0x7FF00000 and _lo(x) != 0):
            return x + x                         # nan
        return _ATANHI[3] + _ATANLO[3] if hx > 0 else -_ATANHI[3] - _ATANLO[3]
    if ix < 0x3FDC0000:                          # |x| < 0.4375
        if ix < 0x3E200000:                      # |x| < 2**-29
            return x
        idx = -1
    else:
        x = abs(x)
        if ix < 0x3FF30000:                      # |x| < 1.1875
            if ix < 0x3FE60000:                  # 7/16 <= |x| < 11/16
                idx = 0
                x = (2.0 * x - 1.0) / (2.0 + x)
            else:                                # 11/16 <= |x| < 19/16
                idx = 1
                x = (x - 1.0) / (x + 1.0)
        elif ix < 0x40038000:                    # |x| < 2.4375
            idx = 2
            x = (x - 1.5) / (1.0 + 1.5 * x)
        else:                                    # 2.4375 <= |x| < 2**66
            idx = 3
            x = -1.0 / x
    z = x * x
    w = z * z
    s1 = z * (_AT[0] + w * (_AT[2] + w * (_AT[4] + w * (_AT[6] + w * (_AT[8] + w * _AT[10])))))
    s2 = w * (_AT[1] + w * (_AT[3] + w * (_AT[5] + w * (_AT[7] + w * _AT[9]))))
    if idx < 0:
        return x - x * (s1 + s2)
    z = _ATANHI[idx] - ((x * (s1 + s2) - _ATANLO[idx]) - x)
    return -z if hx < 0 else z


_PI_O_4 = _d(0x3FE921FB54442D18)    # 7.8539816339744827900e-01
_PI_O_2 = _d(0x3FF921FB54442D18)    # 1.5707963267948965580e+00
_PI_LO = _d(0x3CA1A62633145C07)    # 1.2246467991473531772e-16


def atan2(y, x):
    hx, lx = _hi(x), _lo(x)
    ix = hx & 0x7FFFFFFF
    hy, ly = _hi(y), _lo(y)
    iy = hy & 0x7FFFFFFF
    if x != x or y != y:
        return x + y                             # nan
    if hx == 0x3FF00000 and lx == 0:
        return atan(y)                           # x = 1.0
    m = ((hy >> 31) & 1) | ((hx >> 30) & 2)      # 2 * sign(x) + sign(y)
    if (iy | ly) == 0:                           # y = 0
        if m < 2:
            return y
        return pi if m == 2 else -pi
    if (ix | lx) == 0:                           # x = 0
        return -_PI_O_2 if hy < 0 else _PI_O_2
    if ix == 0x7FF00000:                         # x = inf
        if iy == 0x7FF00000:
            return (_PI_O_4, -_PI_O_4, 3.0 * _PI_O_4, -3.0 * _PI_O_4)[m]
        return (0.0, -0.0, pi, -pi)[m]
    if iy == 0x7FF00000:                         # y = inf
        return -_PI_O_2 if hy < 0 else _PI_O_2
    k = (iy - ix) >> 20
    if k > 60:                                   # |y/x| > 2**60
        z = _PI_O_2 + 0.5 * _PI_LO
    elif hx < 0 and k < -60:                     # |y|/x < -2**60
        z = 0.0
    else:
        z = atan(abs(y / x))
    if m == 0:
        return z
    if m == 1:
        return -z
    if m == 2:
        return pi - (z - _PI_LO)
    return (z - _PI_LO) - pi


# ------------------------------------------------------------------------------------------------ acos (e_acos.c)
_PIO2_HI = _d(0x3FF921FB54442D18)    # 1.57079632679489655800e+00
_PIO2_LO = _d(0x3C91A62633145C07)    # 6.12323399573676603587e-17
_PS0 = _d(0x3FC5555555555555)    # 1.66666666666666657415e-01
_PS1 = -_d(0x3FD4D61203EB6F7D)    # 3.25565818622400915405e-01
_PS2 = _d(0x3FC9C1550E884455)    # 2.01212532134862925881e-01
_PS3 = -_d(0x3FA48228B5688F3B)    # 4.00555345006794114027e-02
_PS4 = _d(0x3F49EFE07501B288)    # 7.91534994289814532176e-04
_PS5 = _d(0x3F023DE10DFDF709)    # 3.47933107596021167570e-05
_QS1 = -_d(0x40033A271C8A2D4B)    # 2.40339491173441421878e+00
_QS2 = _d(0x40002AE59C598AC8)    # 2.02094576023350569471e+00
_QS3 = -_d(0x3FE6066C1B8D0159)    # 6.88283971605453293030e-01
_QS4 = _d(0x3FB3B8C5B12E9282)    # 7.70381505559019352791e-02


def _pq(z):
    p = z * (_PS0 + z * (_PS1 + z * (_PS2 + z * (_PS3 + z * (_PS4 + z * _PS5)))))
    q = 1.0 + z * (_QS1 + z * (_QS2 + z * (_QS3 + z * _QS4)))
    return p / q


def acos(x):
    if x != x:
        return x                                 # nan passes through, as math.acos
    hx = _hi(x)
    ix = hx & 0x7FFFFFFF
    if ix >= 0x3FF00000:                         # |x| >= 1
        if ((ix - 0x3FF00000) | _lo(x)) == 0:
            return 0.0 if hx > 0 else pi + 2.0 * _PIO2_LO
        raise ValueError("math domain error")
    if ix < 0x3FE00000:                          # |x| < 0.5
        if ix <= 0x3C600000:                     # |x| < 2**-57
            return _PIO2_HI + _PIO2_LO
        return _PIO2_HI - (x - (_PIO2_LO - x * _pq(x * x)))
    if hx < 0:                                   # x < -0.5
        z = (1.0 + x) * 0.5
        s = sqrt(z)
        w = _pq(z) * s - _PIO2_LO
        return pi - 2.0 * (s + w)
    z = (1.0 - x) * 0.5                          # x > 0.5
    s = sqrt(z)
    df = _make(_hi(s), 0)
    c = (z - df * df) / (s + df)
    w = _pq(z) * s + c
    return 2.0 * (df + w)
