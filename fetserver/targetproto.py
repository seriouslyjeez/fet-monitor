"""Lines from the new target firmware (firmware/target/core/src/shotline.h), parsed and checked.

    FT1 S b=<boot> q=<seq> t=<ms> n=<N> e=<E> s=<S> w=<W> m=<mask> f=<face us|-> c=<temp 0.1 C|-> k=<settings id>
          d=<dropped> h=<16 hex>

h is HMAC-SHA256 over every byte before " h=", first 8 bytes, with the target's key; "h=-" = no key yet.
"""
from __future__ import annotations

import hmac
import hashlib
import re
from typing import Optional

FIELD_RE = re.compile(r"^[a-z]=\S+$")
SHOT_FIELDS = ("b", "q", "t", "n", "e", "s", "w", "m", "f", "c", "k", "d", "h")


class ProtoError(ValueError):
    pass


def mac(key: bytes, signed: bytes) -> str:
    return hmac.new(key, signed, hashlib.sha256).digest()[:8].hex()


def parse_shot(line: str, key: Optional[bytes] = None) -> dict:
    """The shot as a dict: boot, seq, ms, counts [N, E, S, W] (None for a microphone that did not trigger), mask,
    face_us (None), temp_c (None), settings_id, dropped, and authenticated: True (key matches), False (no key on the
    target, or none given here). A wrong signature, or any malformed field, raises ProtoError."""
    line = line.rstrip("\r\n")
    head, sep, tag = line.rpartition(" h=")
    if not sep or not head.startswith("FT1 S "):
        raise ProtoError("not a shot line")
    parts = head[len("FT1 S "):].split(" ")
    if any(not FIELD_RE.match(p) for p in parts):
        raise ProtoError("malformed field")
    f = dict(p.split("=", 1) for p in parts)
    if tuple(f) != SHOT_FIELDS[:-1]:
        raise ProtoError("fields missing, extra or out of order")
    try:
        mask = int(f["m"], 16)
        counts = [int(f[k]) for k in "nesw"]
        out = {"boot": int(f["b"]), "seq": int(f["q"]), "ms": int(f["t"]), "mask": mask,
               "counts": [c if mask & (1 << i) else None for i, c in enumerate(counts)], "raw_counts": counts,
               "face_us": None if f["f"] == "-" else int(f["f"]), "temp_c": None if f["c"] == "-" else int(f["c"]) / 10,
               "settings_id": f["k"], "dropped": int(f["d"])}
    except ValueError:
        raise ProtoError("a number is not a number")
    if not 0 <= mask <= 15 or not re.fullmatch(r"[0-9a-f]{8}", f["k"]):
        raise ProtoError("mask or settings id out of range")
    if tag == "-":
        out["authenticated"] = False
    elif re.fullmatch(r"[0-9a-f]{16}", tag):
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
TOKEN_RE = re.compile(r"^[a-z_]+$")


def make_command(boot: int, cmd_id: int, verb: str, settings: Optional[dict] = None, key: bytes = b"") -> str:
    """A signed command line (with newline). settings: for "set", {name: int}."""
    if not TOKEN_RE.match(verb):
        raise ProtoError("bad verb")
    head = f"FT1 C b={int(boot)} i={int(cmd_id)} {verb}"
    for k, v in (settings or {}).items():
        if not TOKEN_RE.match(k) or not isinstance(v, int) or isinstance(v, bool):
            raise ProtoError("bad setting")
        head += f" {k}={v}"
    return f"{head} h={mac(key, head.encode())}\n"


def parse_reply(line: str, key: Optional[bytes] = None) -> dict:
    """boot, id, ok, reason ("" when ok), values {name: int, or str for k}, authenticated. A wrong signature raises."""
    line = line.rstrip("\r\n")
    head, sep, tag = line.rpartition(" h=")
    if not sep or not head.startswith("FT1 R "):
        raise ProtoError("not a reply")
    parts = head[len("FT1 R "):].split(" ")
    if len(parts) < 3 or not all(FIELD_RE.match(p) or re.match(r"^[a-z_]+=\S+$", p) for p in parts):
        raise ProtoError("malformed reply")
    f = [p.split("=", 1) for p in parts]
    if [k for k, _ in f[:3]] != ["b", "i", "r"]:
        raise ProtoError("malformed reply")
    values = {}
    for k, v in f[3:]:
        values[k] = v if k == "k" else int(v)
    out = {"boot": int(f[0][1]), "id": int(f[1][1]), "ok": f[2][1] == "ok",
           "reason": "" if f[2][1] == "ok" else f[2][1], "values": values}
    if tag == "-":
        out["authenticated"] = False
    elif key is not None and re.fullmatch(r"[0-9a-f]{16}", tag):
        if not hmac.compare_digest(tag, mac(key, head.encode())):
            raise ProtoError("signature does not match")
        out["authenticated"] = True
    else:
        out["authenticated"] = False
    return out


# ------------------------------------------------------------------------------------------------- hello
# FT1 H b=<boot> fw=<version> rev=<board> k=<settings id> key=<fingerprint|none> h=<mac>   (sent at every start)
HELLO_RE = re.compile(r"^FT1 H b=(\d{1,10}) fw=([0-9A-Za-z.\-]{1,20}) rev=(\d{1,5}) k=([0-9a-f]{8}) key=([0-9a-f]{8}|none)$")


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


def parse_hello(line: str, key: Optional[bytes] = None) -> dict:
    """boot, fw, rev, settings_id, key_fp (None if the target has no key), authenticated. A wrong signature raises."""
    line = line.rstrip("\r\n")
    head, sep, tag = line.rpartition(" h=")
    m = HELLO_RE.match(head) if sep else None
    if not m:
        raise ProtoError("not a hello line")
    out = {"boot": int(m.group(1)), "fw": m.group(2), "rev": int(m.group(3)), "settings_id": m.group(4),
           "key_fp": None if m.group(5) == "none" else m.group(5), "authenticated": False}
    if tag != "-" and not re.fullmatch(r"[0-9a-f]{16}", tag):
        raise ProtoError("malformed signature")
    if key is not None and tag != "-":
        if not hmac.compare_digest(tag, mac(key, head.encode())):
            raise ProtoError("signature does not match")
        out["authenticated"] = True
    return out
