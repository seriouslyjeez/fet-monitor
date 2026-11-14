"""FeT Display images (firmware/display: MicroPython with the display and fetcore frozen in) for the flash station, which
writes them onto CYD shot monitors (ESP32-2432S028R) over USB. One file, written at 0x1000 (the ESP32's boot loader
first, then the partition table and the app). It is accepted only if it looks exactly like one of ours:
* an ESP32 boot loader image first (magic 0xE9, chip id 0: the CYD's ESP32, not an S2/S3/C3);
* exactly one FeT Display version inside ("fet-display/<x.y.z>", lib/fetdisplay.py MARKER);
* it ends before the display's file system (0x200000 in the 4 MB layout: its settings, calibration and a kit's shots).
Stored as <data-dir>/display-firmware/<sha256>.bin with a .json beside them, written atomically.
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

ADDRESS = 0x1000                         # where the image is written
MAX_SIZE = 0x200000 - ADDRESS            # the file system starts at 0x200000
MIN_SIZE = 0x10000
ID_RE = re.compile(rb"fet-display/(\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")


class BadImage(ValueError):
    pass


def inspect(data: bytes) -> dict:
    if len(data) < MIN_SIZE or data[0] != 0xE9:
        raise BadImage("not an ESP32 image to write at 0x1000 (no 0xE9 header)")
    if data[12:14] != b"\0\0":
        raise BadImage("built for another chip, not the CYD's ESP32")
    if len(data) > MAX_SIZE:
        raise BadImage("larger than the room before the display's file system")
    versions = sorted(set(m.group(1).decode() for m in ID_RE.finditer(data)))
    if not versions:
        raise BadImage("not FeT Display (no fet-display/<version> inside)")
    if len(versions) > 1:
        raise BadImage("more than one FeT Display version inside: " + ", ".join(versions))
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "version": versions[0]}


class DisplayFirmwareStore:
    def __init__(self, data_dir: Optional[Path]):
        self.dir = Path(data_dir) / "display-firmware" if data_dir is not None else None

    def add(self, data: bytes, uploaded_by: str = "") -> dict:
        meta = inspect(data)
        if self.dir is None:
            raise BadImage("this server has no data folder for firmware")
        self.dir.mkdir(mode=0o750, exist_ok=True)
        meta.update({"uploaded": int(time.time() * 1000), "uploaded_by": uploaded_by[:60]})
        for suffix, payload in ((".bin", data), (".json", json.dumps(meta).encode())):
            tmp = self.dir / (meta["sha256"] + suffix + ".tmp")
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.dir / (meta["sha256"] + suffix))
        return meta

    def get(self, sha: str) -> Optional[dict]:
        if self.dir is None or not isinstance(sha, str) or not SHA_RE.match(sha):
            return None
        try:
            meta = json.loads((self.dir / (sha + ".json")).read_text())
        except (OSError, ValueError):
            return None
        return meta if (self.dir / (sha + ".bin")).is_file() else None

    def path(self, sha: str) -> Optional[Path]:
        return self.dir / (sha + ".bin") if self.get(sha) else None

    def list(self) -> List[dict]:
        if self.dir is None or not self.dir.is_dir():
            return []
        out = [m for m in (self.get(p.name[:-5]) for p in self.dir.glob("*.json")) if m]
        return sorted(out, key=lambda m: -m.get("uploaded", 0))

    def best(self) -> Optional[dict]:
        imgs = self.list()
        return max(imgs, key=lambda m: (version_tuple(m["version"]) or (0, 0, 0), m.get("uploaded", 0))) if imgs else None

    def delete(self, sha: str) -> bool:
        if not self.get(sha):
            return False
        for suffix in (".bin", ".json"):
            try:
                (self.dir / (sha + suffix)).unlink()
            except FileNotFoundError:
                pass
        return True
