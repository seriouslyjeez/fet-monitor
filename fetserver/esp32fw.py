"""FeT target firmware images for the ESP32-S3 board (firmware/target/esp32), for the flash station and OTA.

One upload: the full flash image the Arduino build makes (fet_target_esp32.ino.merged.bin, 8 MB: bootloader at 0,
partition table at 0x8000, the app at 0x10000). It is accepted only if it looks exactly like one of ours:
* exactly 8 MB, the bootloader and the app both ESP32-S3 images (magic 0xE9, chip id 9), the partition table there;
* the app (found by walking its segments) has a correct checksum and its appended SHA-256 matches;
* the app carries the firmware's identification "fet-target-esp32/<x.y.z>", exactly one version.
The app alone is what OTA sends: a board fetches it by its SHA-256 (the signed "update" command names that hash).
Stored as <data-dir>/esp32-firmware/<app sha256>.full.bin and .app.bin with a .json beside them, written atomically.
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

FULL_SIZE = 8 * 1024 * 1024
APP_OFFSET = 0x10000
APP_MAX = 0x330000                       # an app slot of the 8 MB layout (PartitionScheme=default_8MB)
PARTITIONS = 0x8000
CHIP_ESP32S3 = 9
ID_RE = re.compile(rb"fet-target-esp32/(\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")


class BadImage(ValueError):
    pass


def image_length(data: bytes, off: int = 0) -> int:
    """The length of the ESP32 app/bootloader image at off: header, segments, checksum (padded to 16), appended
    SHA-256 if flagged. Checks the magic, the chip, the checksum and that hash."""
    if len(data) < off + 24 or data[off] != 0xE9:
        raise BadImage("not an ESP32 image (no 0xE9 header)")
    nseg = data[off + 1]
    chip = int.from_bytes(data[off + 12:off + 14], "little")
    if chip != CHIP_ESP32S3:
        raise BadImage(f"built for another chip (id {chip}), not the ESP32-S3")
    if not 1 <= nseg <= 16:
        raise BadImage("implausible number of segments")
    pos, csum = off + 24, 0xEF
    for _ in range(nseg):
        if pos + 8 > len(data):
            raise BadImage("cut short")
        n = int.from_bytes(data[pos + 4:pos + 8], "little")
        if n > len(data) - pos - 8:
            raise BadImage("cut short")
        for b in data[pos + 8:pos + 8 + n]:
            csum ^= b
        pos += 8 + n
    pos += 15 - ((pos - off) % 16)                           # padding, then the checksum byte ends a 16-byte block
    if pos >= len(data) or data[pos] != csum:
        raise BadImage("image checksum is wrong")
    pos += 1
    if data[off + 23] == 1:                                  # SHA-256 of everything before it, appended
        if pos + 32 > len(data) or hashlib.sha256(data[off:pos]).digest() != data[pos:pos + 32]:
            raise BadImage("image hash is wrong")
        pos += 32
    return pos - off


def inspect(full: bytes) -> dict:
    if len(full) != FULL_SIZE:
        raise BadImage("not a full 8 MB image: use fet_target_esp32.ino.merged.bin")
    image_length(full, 0)                                    # the bootloader
    if full[PARTITIONS:PARTITIONS + 2] != b"\xaa\x50":
        raise BadImage("no partition table at 0x8000")
    n = image_length(full, APP_OFFSET)
    if n > APP_MAX:
        raise BadImage("the app is larger than an app slot")
    app = full[APP_OFFSET:APP_OFFSET + n]
    versions = sorted(set(m.group(1).decode() for m in ID_RE.finditer(app)))
    if not versions:
        raise BadImage("not FeT target firmware for the ESP32")
    if len(versions) > 1:
        raise BadImage("more than one firmware version inside the image: " + ", ".join(versions))
    return {"sha256": hashlib.sha256(app).hexdigest(), "size": n, "version": versions[0], "board": "esp32s3",
            "full_sha256": hashlib.sha256(full).hexdigest()}


class Esp32FirmwareStore:
    def __init__(self, data_dir: Optional[Path]):
        self.dir = Path(data_dir) / "esp32-firmware" if data_dir is not None else None

    def add(self, full: bytes, uploaded_by: str = "") -> dict:
        meta = inspect(full)
        if self.dir is None:
            raise BadImage("this server has no data folder for firmware")
        self.dir.mkdir(mode=0o750, exist_ok=True)
        meta.update({"uploaded": int(time.time() * 1000), "uploaded_by": uploaded_by[:60]})
        sha = meta["sha256"]
        app = full[APP_OFFSET:APP_OFFSET + meta["size"]]
        for suffix, payload in ((".app.bin", app), (".full.bin", full), (".json", json.dumps(meta).encode())):
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
        return meta if (self.dir / (sha + ".app.bin")).is_file() and (self.dir / (sha + ".full.bin")).is_file() else None

    def app_path(self, sha: str) -> Optional[Path]:
        """What a board fetches over the air (by the app's SHA-256)."""
        return self.dir / (sha + ".app.bin") if self.get(sha) else None

    def full_path(self, sha: str) -> Optional[Path]:
        """What the flash station writes over USB."""
        return self.dir / (sha + ".full.bin") if self.get(sha) else None

    def list(self) -> List[dict]:
        if self.dir is None or not self.dir.is_dir():
            return []
        out = [m for m in (self.get(p.name[:-5]) for p in self.dir.glob("*.json")) if m]
        return sorted(out, key=lambda m: -m.get("uploaded", 0))

    def best(self) -> Optional[dict]:
        imgs = self.list()
        return max(imgs, key=lambda m: (version_tuple(m["version"]) or (0, 0, 0), m.get("uploaded", 0))) if imgs else None

    def has_full(self, full_sha: str) -> bool:
        """An image with this whole-file SHA-256 is stored (a release lists the full image's hash)."""
        return any(m.get("full_sha256") == full_sha for m in self.list())

    def delete(self, sha: str) -> bool:
        if not self.get(sha):
            return False
        for suffix in (".app.bin", ".full.bin", ".json"):
            try:
                (self.dir / (sha + suffix)).unlink()
            except FileNotFoundError:
                pass
        return True
