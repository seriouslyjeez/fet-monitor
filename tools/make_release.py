#!/usr/bin/env python3
"""Build the server release archive: dist/fet-monitor-<version>.tar.gz and dist/SHA256SUMS.

    python3 tools/make_release.py

The archive unpacks to fet-monitor-<version>/ with install.sh, fetserver/, fetsetup/, deploy/, docs/INSTALL.md, LICENSE, NOTICE and
README.md. Entries are owned by root (uid/gid 0), have normalised permissions and the time of the last commit, so the same
commit always gives the same archive bytes. Refuses to build from a working tree with uncommitted changes unless
--dirty is given (such an archive is for testing only).
Signing is part D (not built yet): the release must not be published as "verified" until it is signed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["install.sh", "LICENSE", "NOTICE", "README.md", "docs/INSTALL.md"]
TREES = ["fetserver", "fetsetup", "deploy"]
EXECUTABLE = {"install.sh", "deploy/migrate-from-freetarget.sh", "deploy/fet-monitor-update.sh"}


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def collect() -> list:
    out = list(FILES)
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*")):
            rel = p.relative_to(ROOT).as_posix()
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith((".pyc", ".db", ".db-wal", ".db-shm")) \
                    and p.name != ".DS_Store":
                out.append(rel)
    for rel in out:
        if not (ROOT / rel).is_file():
            sys.exit(f"missing: {rel}")
    return out


def build(dirty: bool, out: Path) -> Path:
    # Only what goes into the archive matters: changed or untracked files under FILES/TREES. Anything else in the
    # working folder (a stray download, notes) is not packed, so it does not block a release.
    changed = git("status", "--porcelain", "--untracked-files=all", "--", *FILES, *TREES)
    if changed and not dirty:
        sys.exit("uncommitted changes in release files (commit them first, or pass --dirty for a test build):\n" + changed)
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "fetserver/__init__.py").read_text()).group(1)
    mtime = int(git("log", "-1", "--format=%ct"))
    top = f"fet-monitor-{version}"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        dirs = set()
        for rel in collect():
            parts = rel.split("/")[:-1]
            for i in range(len(parts) + 1):
                d = "/".join([top] + parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    ti = tarfile.TarInfo(d)
                    ti.type, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = tarfile.DIRTYPE, 0o755, mtime, 0, 0, "root", "root"
                    tar.addfile(ti)
            data = (ROOT / rel).read_bytes()
            ti = tarfile.TarInfo(f"{top}/{rel}")
            ti.size, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = len(data), mtime, 0, 0, "root", "root"
            ti.mode = 0o755 if rel in EXECUTABLE else 0o644
            tar.addfile(ti, io.BytesIO(data))
    dist = out
    dist.mkdir(parents=True, exist_ok=True)
    path = dist / f"{top}.tar.gz"
    with open(path, "wb") as f:
        with gzip.GzipFile(filename="", fileobj=f, mode="wb", mtime=mtime) as gz:   # no name/time in the gzip header
            gz.write(raw.getvalue())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (dist / "SHA256SUMS").write_text(f"{digest}  {path.name}\n")
    print(f"{path}\n{digest}  {path.name}")
    if dirty:
        print("WARNING: built from uncommitted changes; for testing only", file=sys.stderr)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dirty", action="store_true", help="allow uncommitted changes (test builds only)")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output folder (default: dist/)")
    a = ap.parse_args()
    build(a.dirty, Path(a.out))
