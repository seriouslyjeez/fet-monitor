"""FeT Bridge firmware images (the targets' WiFi bridges), uploaded in Range Control and fetched by the targets.

An upload is accepted only if it looks exactly like one of ours:
* size between MIN_IMAGE and MAX_IMAGE bytes;
* an ESP8266 application image: first byte 0xE9, 1-16 segments, flash mode DOUT (what the ESP-01 needs), a known
  flash size in the header;
* it carries our firmware's identification "fet-monitor-bridge/<x.y.z>" (its HTTP User-Agent), exactly one version.
Images are stored as <data-dir>/firmware/<sha256>.bin with a small .json beside it, written atomically; the SHA-256 is
the name, so a target asks for exactly the bytes that were checked. Nothing else in that folder is ever served.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

MIN_IMAGE = 64 * 1024
MAX_IMAGE = 1024 * 1024
ID_RE = re.compile(rb"fet-monitor-bridge/(\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FLASH_SIZES = {0: 512 * 1024, 1: 256 * 1024, 2: 1 << 20, 3: 2 << 20, 4: 4 << 20, 8: 8 << 20, 9: 16 << 20}
FLASH_MODES = {0: "QIO", 1: "QOUT", 2: "DIO", 3: "DOUT"}


class BadImage(ValueError):
    pass


def inspect(data: bytes) -> dict:
    """Check an upload; returns its description or raises BadImage with a reason a person can act on."""
    if not MIN_IMAGE <= len(data) <= MAX_IMAGE:
        raise BadImage(f"size {len(data)} bytes: a bridge firmware image is {MIN_IMAGE // 1024} KB to {MAX_IMAGE // 1024} KB")
    if data[0] != 0xE9:
        raise BadImage("not an ESP8266 firmware image (use the .bin from Sketch > Export Compiled Binary)")
    if not 1 <= data[1] <= 16:
        raise BadImage("damaged ESP8266 image header")
    mode = FLASH_MODES.get(data[2], "unknown")
    if data[2] != 3:
        raise BadImage(f"flash mode {mode}: the ESP-01 needs DOUT (Tools > Flash Mode > DOUT, then export again)")
    flash = FLASH_SIZES.get(data[3] >> 4)
    if flash is None:
        raise BadImage("unknown flash size in the image header")
    versions = sorted(set(m.group(1).decode() for m in ID_RE.finditer(data)))
    if not versions:
        raise BadImage("not FeT Bridge firmware (0.2.0 or newer)")
    if len(versions) > 1:
        raise BadImage("more than one firmware version inside the image: " + ", ".join(versions))
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "version": versions[0],
            "flash_size": flash, "flash_mode": mode}


def flash_label(n: Optional[int]) -> str:
    if not n:
        return "unknown"
    return f"{n >> 20} MB" if n >= 1 << 20 else f"{n >> 10} KB"


class FirmwareStore:
    def __init__(self, data_dir: Optional[Path]):
        self.dir = Path(data_dir) / "firmware" if data_dir is not None else None

    def _need_dir(self) -> Path:
        if self.dir is None:
            raise BadImage("this server has no data folder for firmware")
        self.dir.mkdir(mode=0o750, exist_ok=True)
        return self.dir

    def add(self, data: bytes, uploaded_by: str = "") -> dict:
        meta = inspect(data)
        d = self._need_dir()
        meta.update({"uploaded": int(time.time() * 1000), "uploaded_by": uploaded_by[:60]})
        sha = meta["sha256"]
        for suffix, payload in ((".bin", data), (".json", json.dumps(meta).encode())):
            tmp = d / (sha + suffix + ".tmp")
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, d / (sha + suffix))
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
        out = [m for m in (self.get(p.stem) for p in self.dir.glob("*.json")) if m]
        return sorted(out, key=lambda m: -m.get("uploaded", 0))

    def delete(self, sha: str) -> bool:
        if not self.get(sha):
            return False
        for suffix in (".bin", ".json"):
            try:
                (self.dir / (sha + suffix)).unlink()
            except FileNotFoundError:
                pass
        return True


def version_tuple(v) -> Optional[tuple]:
    m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def best_for(target: dict, images: List[dict]):
    """The best image for one target: the newest version built for its flash size that fits its free space.
    Returns (image, "") or (None, reason). target: {"ota_space", "flash_size", "bridge_fw"} as the target reported them."""
    if not target.get("ota_space"):
        return None, (f"runs FeT Bridge firmware {target.get('bridge_fw') or '?'}, which cannot update from the Pi: "
                      "install 0.2.0 or newer by USB once")
    flash = target.get("flash_size") or 0
    same = [m for m in images if not flash or m["flash_size"] == flash]
    if not same:
        return None, f"no image for a {flash_label(flash)} build has been uploaded"
    fits = [m for m in same if m["size"] <= target["ota_space"]]
    if not fits:
        return None, f"every {flash_label(flash)} image is larger than its {target['ota_space']} bytes of free space"
    best = max(fits, key=lambda m: (version_tuple(m["version"]) or (0, 0, 0), m.get("uploaded", 0)))
    running = version_tuple(target.get("bridge_fw"))
    if running and version_tuple(best["version"]) <= running:
        return None, f"already up to date ({target.get('bridge_fw')}; newest {flash_label(flash)} image is {best['version']})"
    return best, ""
