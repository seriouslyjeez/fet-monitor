"""Tolerant parser for the lines the freETarget Arduino prints.

The firmware's shot line is not strict JSON (doubled commas before "T" and
"VERSION" in 4.3.0), so lines are normalised before parsing.  Commas inside
strings are never touched.
"""
from __future__ import annotations

from typing import Optional

from .exact import MAX_DIGITS, Dec, loads, parts, to_float, to_hundredths


class ParseError(ValueError):
    pass


def normalise(text: str) -> str:
    """Collapse doubled/leading/trailing commas outside strings so json can parse it."""
    out = []
    in_str = False
    esc = False
    pending = False   # a comma was seen outside a string and is not emitted yet
    last_sig = ""     # last non-space character emitted
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == ",":
            pending = True
            continue
        if ch in " \t\r\n":
            if not pending:
                out.append(ch)
            continue
        if ch in "}]":
            pending = False
            out.append(ch)
            last_sig = ch
            continue
        if pending:
            if last_sig not in ("", "{", "[", ","):
                out.append(",")
                last_sig = ","
            pending = False
        if ch == '"':
            in_str = True
        out.append(ch)
        last_sig = ch
    return "".join(out).strip()


# freETarget prints a position it could not compute as a bare NAN (or INF): as a value, that means "none"
_SPACE = " \t\n\r\f\v"


def _nan_to_null(text: str) -> str:
    """Every ':' followed by optional space, an optional sign and nan/inf (any case), then optional space and one of
    , } ]: the value becomes null. As the regular expression r'(:\s*)[-+]?(?:nan|inf)(?=\s*[,}\]])' (IGNORECASE)
    did, without one (MicroPython's re has no lookahead), anywhere in the text."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        out.append(c)
        i += 1
        if c != ":":
            continue
        j = i
        while j < n and text[j] in _SPACE:
            j += 1
        k = j + 1 if j < n and text[j] in "+-" else j
        if text[k:k + 3].lower() in ("nan", "inf"):
            m = k + 3
            while m < n and text[m] in _SPACE:
                m += 1
            if m < n and text[m] in ",}]":
                out.append(text[i:j] + "null")
                i = k + 3
    return "".join(out)


def _floats(v):
    if isinstance(v, Dec):
        return to_float(v)
    if isinstance(v, dict):
        return {k: _floats(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_floats(x) for x in v]
    return v


def _loads(text: str, floats: bool = False):
    """JSON after normalising; numbers with a point stay Dec (their text) unless floats."""
    norm = normalise(text)
    try:
        v = loads(norm)
    except (ValueError, TypeError) as exc:
        first = exc
        try:                                # only if it did not parse as it is: never touches a string that did
            v = loads(_nan_to_null(norm))
        except (ValueError, TypeError):
            raise ParseError(f"not parseable as JSON after normalising: {first}")
    return _floats(v) if floats else v


class ShotLine:
    """shot, miss; x_h, y_h (hundredths of mm, None for a miss); n, e, s, w; time_s (Arduino uptime when detected,
    seconds; absent on misses); v_ref, temp_c, version, face, name; unplaced (heard, but the target could not place
    it: x/y NAN; no position, not a miss). Read only. A plain class: fetcore also runs on MicroPython."""
    FIELDS = ("shot", "miss", "x_h", "y_h", "n", "e", "s", "w", "time_s", "v_ref", "temp_c", "version", "face", "name",
              "unplaced")

    def __init__(self, shot, miss, x_h, y_h, n, e, s, w, time_s, v_ref, temp_c, version, face, name, unplaced=False):
        self.shot, self.miss, self.x_h, self.y_h = shot, miss, x_h, y_h
        self.n, self.e, self.s, self.w = n, e, s, w
        self.time_s, self.v_ref, self.temp_c, self.version = time_s, v_ref, temp_c, version
        self.face, self.name, self.unplaced = face, name, unplaced

    def _key(self):
        return tuple(getattr(self, k) for k in self.FIELDS)

    def __eq__(self, other):
        return isinstance(other, ShotLine) and self._key() == other._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return "ShotLine(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.FIELDS)


def _int(v) -> int:
    """int(), and for a decimal its integer part (toward zero, as int(Decimal) did)."""
    if isinstance(v, Dec):
        neg, mant, exp = parts(v.text)
        digits = len(str(mant)) + exp
        if digits > MAX_DIGITS:
            raise ParseError("number out of range")
        i = 0 if digits <= 0 else (mant * 10 ** exp if exp >= 0 else mant // 10 ** (-exp))
        return -i if neg else i
    return int(v)


def _opt_int(d, key):
    v = d.get(key)
    return None if v is None else _int(v)


def _opt_float(d, key):
    v = d.get(key)
    return None if v is None else (to_float(v) if isinstance(v, Dec) else float(v))


def parse_shot_line(line: str) -> ShotLine:
    return shot_from_dict(_loads(line))


def shot_from_dict(d) -> ShotLine:
    if not isinstance(d, dict) or "shot" not in d:
        raise ParseError("not a shot line")
    miss = bool(_int(d.get("miss", 0)))
    x_h = y_h = None
    unplaced = False
    if not miss:
        if "x" not in d or "y" not in d:
            raise ParseError("shot line without x/y")
        if d["x"] is None or d["y"] is None:
            unplaced = True
        else:
            x_h, y_h = to_hundredths(d["x"]), to_hundredths(d["y"])
    return ShotLine(
        shot=_int(d["shot"]), miss=miss, x_h=x_h, y_h=y_h,
        n=_opt_int(d, "N"), e=_opt_int(d, "E"), s=_opt_int(d, "S"), w=_opt_int(d, "W"),
        time_s=_opt_float(d, "time"), v_ref=_opt_float(d, "V_REF"), temp_c=_opt_float(d, "T"),
        version=d.get("VERSION"), face=_opt_int(d, "face"), name=d.get("name"), unplaced=unplaced,
    )


def parse_echo(text: str) -> dict:
    """Parse the multi-line {"ECHO":0} settings block into a dict (floats as float)."""
    d = _loads(text, floats=True)
    if not isinstance(d, dict):
        raise ParseError("ECHO block is not an object")
    return d


def echo_summary(d: dict) -> dict:
    """The per-target facts the server cares about."""
    return {
        "arduino_sn": d.get("SN"),
        "calibre_x10": d.get("CALIBREx10"),
        "sensor_mm": d.get("SENSOR"),
        "angle": d.get("ANGLE"),
        "arduino_version": d.get("VERSION"),
        "board_rev": d.get("BD_REV"),
        "name": d.get("NAME"),
    }
