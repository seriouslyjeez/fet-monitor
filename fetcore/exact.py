"""Numbers from text, exactly and the same on every machine (CPython, MicroPython on the CYD).

A shot line carries decimals ("x":-3.61). Turning that text straight into a float is not the same everywhere:
MicroPython's float() is off by one unit in the last place for some inputs (measured 2026-11-13), and decimal.Decimal
does not exist there. So fetcore reads decimal text as an integer mantissa and a power of ten, and:
  * to_hundredths: integer arithmetic only (round half away from zero, as decimal's ROUND_HALF_UP did);
  * to_float: the nearest double by integer arithmetic (ties to even), the same bits as CPython's float(text);
  * loads: JSON whose non-integer numbers come back as Dec (their text), so nothing is rounded before fetcore
    decides how; NaN, Infinity and -Infinity as json.loads gives them.
"""
import math


class Dec:
    """A decimal number as written: str(d) is the text, float(d) and to_hundredths(d) exact."""
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text

    def __repr__(self):
        return "Dec(%r)" % self.text

    def __float__(self):
        return to_float(self.text)

    def __eq__(self, other):
        return isinstance(other, Dec) and self.text == other.text

    def __hash__(self):
        return hash(self.text)


_DIGITS = "0123456789"


def parts(text):
    """(negative, mantissa, exponent) with value = (-1 if negative) * mantissa * 10**exponent, or ValueError.
    Accepts what JSON and Python print: [+-]digits[.digits][e[+-]digits] (".5" and "5." too)."""
    s = text.strip()
    neg = False
    if s[:1] in ("+", "-"):
        neg = s[0] == "-"
        s = s[1:]
    exp = 0
    for e in ("e", "E"):
        if e in s:
            s, _, es = s.partition(e)
            sign = 1
            if es[:1] in ("+", "-"):
                sign = -1 if es[0] == "-" else 1
                es = es[1:]
            if not es or any(c not in _DIGITS for c in es):
                raise ValueError("not a decimal number: %r" % text)
            exp = sign * int(es)
            break
    whole, dot, frac = s.partition(".")
    if not (whole or frac) or any(c not in _DIGITS for c in whole) or any(c not in _DIGITS for c in frac):
        raise ValueError("not a decimal number: %r" % text)
    mant = int((whole or "0") + frac)
    return neg, mant, exp - len(frac)


def _value_of(value):
    """Text for a number given as int, str, Dec, float (its shortest repr, as CPython prints it) or Decimal."""
    if isinstance(value, Dec):
        return value.text
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("not a finite number")
        return repr(value)
    return str(value)                                   # Decimal (servers only)


MAX_DIGITS = 20          # integer parts longer than this (1e20 mm) are refused: never a target's number


def _magnitude(mant, exp):
    """The number of digits before the point of mant * 10**exp (may be <= 0)."""
    return len(str(mant)) + exp


def to_hundredths(value):
    """A length in millimetres (int, str, Dec, float or Decimal) to integer hundredths, rounding half away from
    zero. Exact for text; a float goes through its repr (on MicroPython pass the text, never a float). Refuses
    lengths of 1e20 mm or more (a malformed line must not make a giant integer)."""
    if isinstance(value, bool):
        raise ValueError("not a number")
    if isinstance(value, int):
        return value * 100
    neg, mant, exp = parts(_value_of(value))
    if mant == 0 or _magnitude(mant, exp) < -2:          # below 0.001 mm: rounds to 0
        return 0
    if _magnitude(mant, exp) > MAX_DIGITS:
        raise ValueError("number out of range")
    exp += 2
    if exp >= 0:
        h = mant * 10 ** exp
    else:
        d = 10 ** (-exp)
        h, r = divmod(mant, d)
        if 2 * r >= d:
            h += 1
    return -h if neg else h


def _nbits(n):
    return len(bin(n)) - 2                              # int.bit_length (missing on MicroPython), n > 0


def _ratio(num, den):
    """num/den (positive integers) as the nearest double, ties to even: integer arithmetic, then one exact ldexp."""
    shift = 54 - (_nbits(num) - _nbits(den))
    while True:
        q, r = divmod(num << shift, den) if shift >= 0 else divmod(num, den << -shift)
        n = _nbits(q)
        if n == 54:
            break
        shift += 1 if n < 54 else -1
    drop = 1                                            # q has 54 bits: keep 53 ...
    lsb = 1 - shift                                     # ... worth 2**lsb each
    if lsb < -1074:                                     # below the normal range: fewer bits, as a subnormal
        drop += -1074 - lsb
        lsb = -1074
    m = q >> drop if drop < 60 else 0
    dropped = q & ((1 << drop) - 1)
    half = 1 << (drop - 1)
    if dropped > half or (dropped == half and (r or m & 1)):    # nearest, ties to even
        m += 1
    try:
        return math.ldexp(float(m), lsb)                # exact: m fits 53 bits
    except OverflowError:
        return math.inf


def to_float(value):
    """The double nearest a decimal (correctly rounded: the same bits as CPython's float(text)), on any machine."""
    neg, mant, exp = parts(_value_of(value))
    if mant == 0 or _magnitude(mant, exp) < -330:        # below half the smallest double: 0
        v = 0.0
    elif _magnitude(mant, exp) > 310:                   # beyond the largest double
        v = math.inf
    elif exp >= 0:
        v = _ratio(mant * 10 ** exp, 1)
    else:
        v = _ratio(mant, 10 ** (-exp))
    return -v if neg else v


# ------------------------------------------------------------------------------------------------------ JSON
class _Reader:
    def __init__(self, text):
        self.s = text
        self.i = 0

    def fail(self, what):
        raise ValueError("%s at character %d" % (what, self.i))

    def ws(self):
        s, i = self.s, self.i
        while i < len(s) and s[i] in " \t\r\n":
            i += 1
        self.i = i

    def value(self):
        self.ws()
        s, i = self.s, self.i
        if i >= len(s):
            self.fail("value expected")
        c = s[i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c == '"':
            return self.string()
        for word, v in (("true", True), ("false", False), ("null", None), ("NaN", float("nan")),
                        ("Infinity", float("inf")), ("-Infinity", float("-inf"))):
            if s.startswith(word, i):
                self.i = i + len(word)
                return v
        return self.number()

    def number(self):
        s, i = self.s, self.i
        j = i
        if j < len(s) and s[j] == "-":
            j += 1
        start = j
        while j < len(s) and s[j] in _DIGITS:
            j += 1
        if j == start or (s[start] == "0" and j - start > 1):
            self.fail("bad number")
        is_int = True
        if j < len(s) and s[j] == ".":
            j += 1
            k = j
            while j < len(s) and s[j] in _DIGITS:
                j += 1
            if j == k:
                self.fail("bad number")
            is_int = False
        if j < len(s) and s[j] in "eE":
            j += 1
            if j < len(s) and s[j] in "+-":
                j += 1
            k = j
            while j < len(s) and s[j] in _DIGITS:
                j += 1
            if j == k:
                self.fail("bad number")
            is_int = False
        self.i = j
        text = s[i:j]
        return int(text) if is_int else Dec(text)

    def string(self):
        s = self.s
        i = self.i + 1
        out = []
        while True:
            if i >= len(s):
                self.fail("unterminated string")
            c = s[i]
            if c == '"':
                self.i = i + 1
                return "".join(out)
            if c == "\\":
                i += 1
                if i >= len(s):
                    self.fail("unterminated string")
                e = s[i]
                if e == "u":
                    code = self._hex4(i + 1)
                    i += 4
                    if 0xD800 <= code <= 0xDBFF and s[i + 1:i + 3] == "\\u":
                        low = self._hex4(i + 3)
                        if 0xDC00 <= low <= 0xDFFF:
                            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                            i += 6
                    out.append(chr(code))
                elif e in '"\\/':
                    out.append(e)
                elif e in "bfnrt":
                    out.append({"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}[e])
                else:
                    self.i = i
                    self.fail("bad escape")
                i += 1
                continue
            if ord(c) < 0x20:
                self.i = i
                self.fail("control character in string")
            out.append(c)
            i += 1

    def _hex4(self, at):
        h = self.s[at:at + 4]
        if len(h) != 4 or any(c not in "0123456789abcdefABCDEF" for c in h):
            self.i = at
            self.fail("bad \\u escape")
        return int(h, 16)

    def obj(self):
        self.i += 1
        out = {}
        self.ws()
        if self.s[self.i:self.i + 1] == "}":
            self.i += 1
            return out
        while True:
            self.ws()
            if self.s[self.i:self.i + 1] != '"':
                self.fail("name expected")
            k = self.string()
            self.ws()
            if self.s[self.i:self.i + 1] != ":":
                self.fail("':' expected")
            self.i += 1
            out[k] = self.value()
            self.ws()
            c = self.s[self.i:self.i + 1]
            self.i += 1
            if c == "}":
                return out
            if c != ",":
                self.i -= 1
                self.fail("',' or '}' expected")

    def arr(self):
        self.i += 1
        out = []
        self.ws()
        if self.s[self.i:self.i + 1] == "]":
            self.i += 1
            return out
        while True:
            out.append(self.value())
            self.ws()
            c = self.s[self.i:self.i + 1]
            self.i += 1
            if c == "]":
                return out
            if c != ",":
                self.i -= 1
                self.fail("',' or ']' expected")


def loads(text):
    """JSON as json.loads reads it, except that numbers with a point or an exponent come back as Dec (their text)."""
    r = _Reader(text)
    v = r.value()
    r.ws()
    if r.i != len(text):
        r.fail("extra data")
    return v
