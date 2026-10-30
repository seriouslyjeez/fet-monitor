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
