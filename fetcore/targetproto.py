"""Lines from the new target firmware (firmware/target/core/src/shotline.h), parsed and checked.

    FT1 S b=<boot> q=<seq> t=<ms> n=<N> e=<E> s=<S> w=<W> m=<mask> f=<face us|-> c=<temp 0.1 C|-> k=<settings id>
          d=<dropped> [r=<counts per second>] h=<16 hex>

r (protocol v2, ESP32 firmware 0.3.2+): the rate of n/e/s/w; absent, the board revision's clock applies.

h is HMAC-SHA256 over every byte before " h=", first 8 bytes, with the target's key; "h=-" = no key yet.
"""
from __future__ import annotations

import hmac
import hashlib
from typing import Optional

# No regular expressions: MicroPython's re has no fullmatch and no {n} counts, and Python's \d, \S and int() accept
# more than ASCII (other scripts' digits, "1_000", "+5"). Lines are ASCII; these checks give the same answer on both.
_HEX = "0123456789abcdef"
_DIGITS = "0123456789"
_LOWER = "abcdefghijklmnopqrstuvwxyz"


def _is_hex(s: str, n: int) -> bool:
    return len(s) == n and all(c in _HEX for c in s)


def _visible(s: str) -> bool:
    """Non-empty printable ASCII without spaces."""
    return bool(s) and all("!" <= c <= "~" for c in s)


def _field(p: str) -> bool:
    """<one lower-case letter>=<visible text>"""
    return len(p) >= 3 and p[0] in _LOWER and p[1] == "=" and _visible(p[2:])


def _token(s: str) -> bool:
    return bool(s) and all(c in _LOWER or c == "_" for c in s)


def _num(s: str) -> int:
    """A decimal integer as the firmware prints it: an optional minus and ASCII digits."""
    d = s[1:] if s[:1] == "-" else s
    if not d or any(c not in _DIGITS for c in d):
        raise ValueError("not a number: %r" % s)
    return int(s)


def _hexnum(s: str) -> int:
    if not s or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError("not a hex number: %r" % s)
    return int(s, 16)
SHOT_FIELDS = ("b", "q", "t", "n", "e", "s", "w", "m", "f", "c", "k", "d", "h")
RATE_MIN_HZ, RATE_MAX_HZ = 1_000_000, 1_000_000_000      # a counter rate a target may report (r=)


class ProtoError(ValueError):
    pass


def mac(key: bytes, signed: bytes) -> str:
    return hmac.new(key, signed, hashlib.sha256).digest()[:8].hex()


def parse_shot(line: str, key: Optional[bytes] = None) -> dict:
    """The shot as a dict: boot, seq, ms, counts [N, E, S, W] (None for a microphone that did not trigger), mask,
    face_us (None), temp_c (None), settings_id, dropped, rate_hz (None: the board revision's clock), and authenticated:
    True (key matches), False (no key on the target, or none given here). A wrong signature, or any malformed field,
    raises ProtoError."""
    line = line.rstrip("\r\n")
    head, sep, tag = line.rpartition(" h=")
    if not sep or not head.startswith("FT1 S "):
        raise ProtoError("not a shot line")
    parts = head[len("FT1 S "):].split(" ")
    if any(not _field(p) for p in parts):
        raise ProtoError("malformed field")
    pairs = [p.split("=", 1) for p in parts]
    f = dict(pairs)
    if tuple(k for k, _ in pairs) not in (SHOT_FIELDS[:-1], SHOT_FIELDS[:-1] + ("r",)):   # not tuple(f): MicroPython's dicts keep no order
        raise ProtoError("fields missing, extra or out of order")
    try:
        mask = _hexnum(f["m"])
        counts = [_num(f[k]) for k in "nesw"]
        out = {"boot": _num(f["b"]), "seq": _num(f["q"]), "ms": _num(f["t"]), "mask": mask,
               "counts": [c if mask & (1 << i) else None for i, c in enumerate(counts)], "raw_counts": counts,
               "face_us": None if f["f"] == "-" else _num(f["f"]), "temp_c": None if f["c"] == "-" else _num(f["c"]) / 10,
               "settings_id": f["k"], "dropped": _num(f["d"]), "rate_hz": _num(f["r"]) if "r" in f else None}
    except ValueError:
        raise ProtoError("a number is not a number")
    if not 0 <= mask <= 15 or not _is_hex(f["k"], 8):
        raise ProtoError("mask or settings id out of range")
    if out["rate_hz"] is not None and not RATE_MIN_HZ <= out["rate_hz"] <= RATE_MAX_HZ:
        raise ProtoError("counter rate out of range")
    if tag == "-":
        out["authenticated"] = False
    elif _is_hex(tag, 16):
        if key is None:
            out["authenticated"] = False
        elif hmac.compare_digest(tag, mac(key, head.encode())):
            out["authenticated"] = True
        else:
            raise ProtoError("signature does not match")
    else:
        raise ProtoError("malformed signature")
    return out


# ------------------------------------------------------------------------------------------------ commands
# FT1 C b=<boot> i=<id> <verb> [key=value ...] h=<mac>     (firmware/target/core/src/commands.h)
# FT1 R b=<boot> i=<id> r=<ok|reason> [key=value ...] h=<mac>


def make_command(boot: int, cmd_id: int, verb: str, settings: Optional[dict] = None, key: bytes = b"") -> str:
    """A signed command line (with newline). settings: for "set", {name: int}."""
    if not _token(verb):
        raise ProtoError("bad verb")
    head = f"FT1 C b={int(boot)} i={int(cmd_id)} {verb}"
    for k, v in (settings or {}).items():
        if not _token(k) or not isinstance(v, int) or isinstance(v, bool):
            raise ProtoError("bad setting")
        head += f" {k}={v}"
    return f"{head} h={mac(key, head.encode())}\n"


def make_update(boot: int, cmd_id: int, sha256: str, size: int, key: bytes) -> str:
    """The signed "update" command: fetch this image from FeT Monitor and install it only if its SHA-256 matches."""
    if not _is_hex(sha256 or "", 64) or not isinstance(size, int) or isinstance(size, bool) or not 0 < size < 2 ** 32:
        raise ProtoError("bad image")
    head = f"FT1 C b={int(boot)} i={int(cmd_id)} update sha={sha256} size={size}"
    return f"{head} h={mac(key, head.encode())}\n"


def _reply_field(p: str) -> bool:
    """<lower-case letters and _>=<visible text>"""
    k, eq, v = p.partition("=")
    return _token(k) and eq == "=" and _visible(v)


def parse_reply(line: str, key: Optional[bytes] = None) -> dict:
    """boot, id, ok, reason ("" when ok), values {name: int, or str for k}, authenticated. A wrong signature raises."""
    line = line.rstrip("\r\n")
    head, sep, tag = line.rpartition(" h=")
    if not sep or not head.startswith("FT1 R "):
        raise ProtoError("not a reply")
    parts = head[len("FT1 R "):].split(" ")
    if len(parts) < 3 or not all(_reply_field(p) for p in parts):
        raise ProtoError("malformed reply")
    f = [p.split("=", 1) for p in parts]
    if [k for k, _ in f[:3]] != ["b", "i", "r"]:
        raise ProtoError("malformed reply")
    values = {}
    try:
        for k, v in f[3:]:
            values[k] = v if k == "k" else _num(v)
        out = {"boot": _num(f[0][1]), "id": _num(f[1][1]), "ok": f[2][1] == "ok",
               "reason": "" if f[2][1] == "ok" else f[2][1], "values": values}
    except ValueError:
        raise ProtoError("a number is not a number")
    if tag == "-":
        out["authenticated"] = False
    elif key is not None and _is_hex(tag, 16):
        if not hmac.compare_digest(tag, mac(key, head.encode())):
            raise ProtoError("signature does not match")
        out["authenticated"] = True
    else:
        out["authenticated"] = False
    return out


# ------------------------------------------------------------------------------------------------- hello
# FT1 H b=<boot> fw=<version> rev=<board> k=<settings id> key=<fingerprint|none> h=<mac>   (sent at every start)
_FW = _DIGITS + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + _LOWER + ".-"


def _hello_fields(head: str):
    """(boot, fw, rev, settings id, key) from "FT1 H b=<1-10 digits> fw=<1-20 of 0-9A-Za-z.-> rev=<1-5 digits>
    k=<8 hex> key=<8 hex|none>", or None."""
    if not head.startswith("FT1 H "):
        return None
    parts = head[len("FT1 H "):].split(" ")
    names = ("b=", "fw=", "rev=", "k=", "key=")
    if len(parts) != 5 or any(not p.startswith(n) for p, n in zip(parts, names)):
        return None
    b, fw, rev, k, key = (p[len(n):] for p, n in zip(parts, names))
    if not (1 <= len(b) <= 10 and all(c in _DIGITS for c in b)) or not (1 <= len(fw) <= 20 and all(c in _FW for c in fw)):
        return None
    if not (1 <= len(rev) <= 5 and all(c in _DIGITS for c in rev)) or not _is_hex(k, 8):
        return None
    if not (_is_hex(key, 8) or key == "none"):
        return None
    return b, fw, rev, k, key


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).digest().hex()[:8]


def parse_hello(line: str, key: Optional[bytes] = None) -> dict:
    """boot, fw, rev, settings_id, key_fp (None if the target has no key), authenticated. A wrong signature raises."""
    line = line.rstrip("\r\n")
    head, sep, tag = line.rpartition(" h=")
    m = _hello_fields(head) if sep else None
    if not m:
        raise ProtoError("not a hello line")
    out = {"boot": int(m[0]), "fw": m[1], "rev": int(m[2]), "settings_id": m[3],
           "key_fp": None if m[4] == "none" else m[4], "authenticated": False}
    if tag != "-" and not _is_hex(tag, 16):
        raise ProtoError("malformed signature")
    if key is not None and tag != "-":
        if not hmac.compare_digest(tag, mac(key, head.encode())):
            raise ProtoError("signature does not match")
        out["authenticated"] = True
    return out
