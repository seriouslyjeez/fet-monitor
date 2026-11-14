#!/usr/bin/env python3
"""What is published, what is in the code, and what changed since: one line per product (user 2026-11-14: releases
were described as unpublished after they had been published, and a published version was rebuilt).

    python3 tools/release_status.py

For each product: the newest tag on GitHub, the version in the code, whether that version is already tagged, and the
commits that touched the product since its newest tag. Then what to do: nothing, "bump the version" (changed since a
published version), or "ready to release" (a new version in the code). Also whether the newest server release's SD
card image is signed. It changes nothing but fetching GitHub's tags; it asks git and GitHub (gh).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# product: (tag prefix, file and pattern of its version, paths whose changes need a new release)
PRODUCTS = {
    "FeT Monitor (server)": ("v", "fetserver/__init__.py", r'^__version__ = "([0-9.]+)"',
                             ["fetserver", "fetcore", "deploy", "image", "install.sh", "bin"]),
    "FeT Bridge (ESP-01)": ("fw-", "firmware/fet_bridge/src/platform/build_config.h", r'^#define FET_FW_VERSION "([0-9.]+)"$',
                            ["firmware/fet_bridge"]),
    "FeT target (Mega)": ("tgt-", "firmware/target/mega/fet_target_mega/fet_target_mega.ino", r'^#define FW_VERSION "([0-9.]+)"',
                          ["firmware/target/mega", "firmware/target/core"]),
    "FeT target (ESP32)": ("esp32-", "firmware/target/esp32/fet_target_esp32/fet_target_esp32.ino", r'^#define FW_VERSION "([0-9.]+)"',
                           ["firmware/target/esp32", "firmware/target/core", "firmware/fet_bridge/src/core"]),
    "FeT Display (CYD)": ("display-", "firmware/display/lib/fetdisplay.py", r'^VERSION = "([0-9.]+)"',
                          ["firmware/display", "fetcore"]),
}


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def vt(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def remote_tags() -> set:
    out = git("ls-remote", "--tags", "origin")
    return {ln.split("refs/tags/", 1)[1] for ln in out.splitlines() if "refs/tags/" in ln and not ln.endswith("^{}")}


REVIEWED = ROOT / "tools" / "release_reviewed.json"


def reviewed() -> dict:
    try:
        return {k: v for k, v in json.loads(REVIEWED.read_text()).items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def status(tags: set) -> list:
    rows, seen = [], reviewed()
    for name, (prefix, vfile, pattern, paths) in PRODUCTS.items():
        m = re.search(pattern, (ROOT / vfile).read_text(), re.M)
        code = m.group(1) if m else "?"
        versions = sorted((t[len(prefix):] for t in tags if t.startswith(prefix) and re.fullmatch(r"\d+\.\d+\.\d+", t[len(prefix):])), key=vt)
        newest = versions[-1] if versions else None
        here = [p for p in paths if (ROOT / p).exists()]
        rev = seen.get(name) if newest and seen.get(name, {}).get("tag") == f"{prefix}{newest}" else None
        since = rev["through"] if rev else f"{prefix}{newest}"
        changed = git("log", "--oneline", f"{since}..HEAD", "--", *here).splitlines() if newest and here else []
        if code == "?":
            todo = "cannot read the version in " + vfile
        elif newest and vt(code) < vt(newest):
            todo = f"the code says {code}, older than the newest {newest}"
        elif code in versions:
            todo = (f"{len(changed)} commit(s) since {since}: bump the version, then release (or record why not in "
                    "tools/release_reviewed.json)" if changed and code == newest
                    else ("published; later commits reviewed: no release needed (" + rev["why"] + ")" if rev else "published; nothing new")
                    if code == newest else f"the code says {code}, older than the newest {newest}")
        else:
            todo = f"{code} is not published yet: ready to release" + (f" ({len(changed)} commit(s) since {prefix}{newest})" if newest else "")
        rows.append({"product": name, "published": f"{prefix}{newest}" if newest else "none", "code": code, "changed": changed, "todo": todo})
    return rows


def image_signed(tag: str):
    """True/False: the server release's SD card image has its signature; None: no image (yet) or cannot ask."""
    try:
        r = subprocess.run(["gh", "release", "view", tag, "--json", "assets"], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    names = {a["name"] for a in json.loads(r.stdout)["assets"]}
    if "IMAGE-SHA256SUMS" not in names:
        return None
    return "IMAGE-SHA256SUMS.sig" in names


def main() -> int:
    try:
        git("fetch", "--quiet", "--tags", "origin")        # tags made by "gh release create" exist on GitHub first
        tags = remote_tags()
    except subprocess.CalledProcessError as exc:
        print("cannot ask GitHub (git ls-remote): " + (exc.stderr or "").strip(), file=sys.stderr)
        return 1
    rows = status(tags)
    w = max(len(r["product"]) for r in rows)
    for r in rows:
        print(f"{r['product']:<{w}}  published {r['published']:<14} code {r['code']:<8} {r['todo']}")
        for c in r["changed"][:6]:
            print(" " * (w + 2) + "  " + c[:100])
        if len(r["changed"]) > 6:
            print(" " * (w + 2) + f"  ... and {len(r['changed']) - 6} more")
    server = next(r for r in rows if r["product"].startswith("FeT Monitor"))
    if server["published"] != "none":
        signed = image_signed(server["published"])
        print(f"\nSD card image of {server['published']}: " + {True: "signed", False: "built, NOT signed yet "
              f"(python3 tools/release.py image {server['published'][1:]} --publish)", None: "none attached (yet)"}[signed])
    return 0


if __name__ == "__main__":
    sys.exit(main())
