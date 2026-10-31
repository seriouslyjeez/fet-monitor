"""FeT target firmware images for the Arduino Mega (firmware/target/mega), for the flash station.

An image is accepted only if it looks exactly like one of ours:
* Intel HEX: every record well formed with a correct checksum, an end record, nothing after it;
* every byte below the Mega's boot loader (0x3E000: 248 KB), so it can never touch the boot loader (the
  "with_bootloader" hex from the Arduino build is refused);
* it carries the firmware's identification "fet-target-mega/<x.y.z>", exactly one version.
Stored as <data-dir>/target-firmware/<sha256>.hex with a .json beside it, written atomically, like firmware.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from .firmware import SHA_RE, version_tuple

APP_END = 0x3E000                       # the ATmega2560's application area ends where the 8 KB boot loader starts
MAX_HEX = 1024 * 1024
ID_RE = re.compile(rb"fet-target-mega/(\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")


class BadImage(ValueError):
    pass


def parse_hex(text: bytes) -> bytes:
    """The program bytes of an Intel HEX file (gaps as 0xFF), checked record by record."""
    mem = {}
    base = 0
    ended = False
    for no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if ended:
            raise BadImage(f"line {no}: data after the end record")
        if line[:1] != b":" or len(line) < 11 or len(line) % 2 == 0:
            raise BadImage(f"line {no}: not an Intel HEX record")
        try:
            rec = bytes.fromhex(line[1:].decode("ascii"))
        except ValueError:
            raise BadImage(f"line {no}: not an Intel HEX record")
        n, addr, rtype = rec[0], rec[1] << 8 | rec[2], rec[3]
        if len(rec) != n + 5:
            raise BadImage(f"line {no}: wrong length")
        if sum(rec) & 0xFF:
            raise BadImage(f"line {no}: checksum")
        data = rec[4:4 + n]
        if rtype == 0:
            for i, b in enumerate(data):
                a = base + addr + i
                if a >= APP_END:
                    raise BadImage("writes into the boot loader area: use the .hex without the boot loader")
                mem[a] = b
        elif rtype == 1:
            ended = True
        elif rtype == 2 and n == 2:
            base = (data[0] << 8 | data[1]) << 4
        elif rtype == 4 and n == 2:
            base = (data[0] << 8 | data[1]) << 16
        elif rtype in (3, 5):
            pass                                          # start address: meaningless for avrdude, harmless
        else:
            raise BadImage(f"line {no}: unknown record type {rtype}")
    if not ended:
        raise BadImage("no end record: the file is cut short")
    if not mem:
        raise BadImage("no program in the file")
    top = max(mem) + 1
    out = bytearray(b"\xff" * top)
    for a, b in mem.items():
        out[a] = b
    return bytes(out)


def inspect(text: bytes) -> dict:
    if len(text) > MAX_HEX:
        raise BadImage("too large for an Arduino Mega program")
    prog = parse_hex(text)
    versions = sorted(set(m.group(1).decode() for m in ID_RE.finditer(prog)))
    if not versions:
        raise BadImage("not FeT target firmware for the Arduino Mega")
    if len(versions) > 1:
        raise BadImage("more than one firmware version inside the image: " + ", ".join(versions))
    return {"sha256": hashlib.sha256(text).hexdigest(), "size": len(prog), "version": versions[0], "board": "mega2560"}


class TargetFirmwareStore:
    def __init__(self, data_dir: Optional[Path]):
        self.dir = Path(data_dir) / "target-firmware" if data_dir is not None else None

    def add(self, text: bytes, uploaded_by: str = "") -> dict:
        meta = inspect(text)
        if self.dir is None:
            raise BadImage("this server has no data folder for firmware")
        self.dir.mkdir(mode=0o750, exist_ok=True)
        meta.update({"uploaded": int(time.time() * 1000), "uploaded_by": uploaded_by[:60]})
        sha = meta["sha256"]
        for suffix, payload in ((".hex", text), (".json", json.dumps(meta).encode())):
            tmp = self.dir / (sha + suffix + ".tmp")
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.dir / (sha + suffix))
        return meta

    def get(self, sha: str) -> Optional[dict]:
        if self.dir is None or not isinstance(sha, str) or not SHA_RE.match(sha):
            return None
        try:
            meta = json.loads((self.dir / (sha + ".json")).read_text())
        except (OSError, ValueError):
            return None
        return meta if (self.dir / (sha + ".hex")).is_file() else None

    def path(self, sha: str) -> Optional[Path]:
        return self.dir / (sha + ".hex") if self.get(sha) else None

    def list(self) -> List[dict]:
        if self.dir is None or not self.dir.is_dir():
            return []
        out = [m for m in (self.get(p.stem) for p in self.dir.glob("*.json")) if m]
        return sorted(out, key=lambda m: -m.get("uploaded", 0))

    def best(self) -> Optional[dict]:
        imgs = self.list()
        return max(imgs, key=lambda m: (version_tuple(m["version"]) or (0, 0, 0), m.get("uploaded", 0))) if imgs else None

    def delete(self, sha: str) -> bool:
        if not self.get(sha):
            return False
        for suffix in (".hex", ".json"):
            try:
                (self.dir / (sha + suffix)).unlink()
            except FileNotFoundError:
                pass
        return True
