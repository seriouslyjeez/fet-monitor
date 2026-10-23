"""Tolerant parser for the lines the freETarget Arduino prints.

The firmware's shot line is not strict JSON (doubled commas before "T" and
"VERSION" in 4.3.0), so lines are normalised before parsing.  Commas inside
strings are never touched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .scoring import to_hundredths


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


def _loads(text: str, floats=Decimal):
    try:
        return json.loads(normalise(text), parse_float=floats)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"not parseable as JSON after normalising: {exc}") from exc


@dataclass(frozen=True)
class ShotLine:
    shot: int
    miss: bool
    x_h: Optional[int]          # hundredths of mm, None for a miss
    y_h: Optional[int]
    n: Optional[int]
    e: Optional[int]
    s: Optional[int]
    w: Optional[int]
    time_s: Optional[float]     # Arduino uptime when detected, seconds (absent on misses)
    v_ref: Optional[float]
    temp_c: Optional[float]
    version: Optional[str]
    face: Optional[int]
    name: Optional[str]


def _opt_int(d, key):
    v = d.get(key)
    return None if v is None else int(v)


def _opt_float(d, key):
    v = d.get(key)
    return None if v is None else float(v)


def parse_shot_line(line: str) -> ShotLine:
    return shot_from_dict(_loads(line))


def shot_from_dict(d) -> ShotLine:
    if not isinstance(d, dict) or "shot" not in d:
        raise ParseError("not a shot line")
    miss = bool(int(d.get("miss", 0)))
    x_h = y_h = None
    if not miss:
        if "x" not in d or "y" not in d:
            raise ParseError("shot line without x/y")
        x_h, y_h = to_hundredths(d["x"]), to_hundredths(d["y"])
    return ShotLine(
        shot=int(d["shot"]), miss=miss, x_h=x_h, y_h=y_h,
        n=_opt_int(d, "N"), e=_opt_int(d, "E"), s=_opt_int(d, "S"), w=_opt_int(d, "W"),
        time_s=_opt_float(d, "time"), v_ref=_opt_float(d, "V_REF"), temp_c=_opt_float(d, "T"),
        version=d.get("VERSION"), face=_opt_int(d, "face"), name=d.get("name"),
    )


def parse_echo(text: str) -> dict:
    """Parse the multi-line {"ECHO":0} settings block into a dict (floats as float)."""
    d = _loads(text, floats=float)
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
