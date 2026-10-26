#!/usr/bin/env python3
"""Build, sign and (optionally) publish a release.

    python3 tools/release.py server [--notes NOTES.md] [--publish]
    python3 tools/release.py firmware 0.2.2 [--notes NOTES.md] [--publish]

server   : the server archive from the committed tree (tools/make_release.py) -> dist/server-<ver>/
firmware : the collected images dist/firmware/fet-bridge-<ver>-*.bin (tools/collect_firmware.py) -> dist/fw-<ver>/

Each folder gets SHA256SUMS and SHA256SUMS.sig (ssh-keygen -Y sign with your release key; it asks for the key's
passphrase), and the signature is checked against fetserver/release_signers before anything is published. Without
--publish it prints the `gh release create` command; with --publish it runs it after you confirm. Tags: v<ver> for the
server, fw-<ver> for FeT Bridge firmware (that is what the Pi looks for).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetserver.firmware import BadImage, inspect  # noqa: E402
from fetserver.releases import SIGN_NAMESPACE, SIGNERS, ReleaseError, check_archive, parse_sums, verify_signature  # noqa: E402

KEY = Path.home() / ".ssh" / "fet-monitor-release"


def sums_for(folder: Path, names) -> Path:
    lines = [f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}" for n in sorted(names)]
    p = folder / "SHA256SUMS"
    p.write_text("\n".join(lines) + "\n")
    return p


def sign(sums: Path, key: Path) -> None:
    sig = sums.with_name("SHA256SUMS.sig")
    if sig.exists():
        sig.unlink()
    print(f"Signing {sums} with {key} (enter the key's passphrase):")
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", SIGN_NAMESPACE, str(sums)], check=True)
    verify_signature(sums.read_bytes(), sig.read_bytes(), SIGNERS)      # the same check the Pi makes
    print("Signature verified against fetserver/release_signers.")


def server(args) -> tuple:
    out = subprocess.run([sys.executable, str(ROOT / "tools/make_release.py")], capture_output=True, text=True)
    if out.returncode:
        sys.exit(out.stderr.strip() or "make_release failed")
    archive = Path(out.stdout.splitlines()[0])
    version = re.search(r"fet-monitor-(\d+\.\d+\.\d+)\.tar\.gz$", archive.name).group(1)
    check_archive(archive.read_bytes(), version)
    folder = ROOT / "dist" / f"server-{version}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    shutil.copyfile(archive, folder / archive.name)
    return folder, [archive.name], f"v{version}", f"FeT Monitor {version}"


def firmware(args) -> tuple:
    v = args.version
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", v or ""):
        sys.exit("give the firmware version, e.g. 0.2.2")
    images = sorted((ROOT / "dist/firmware").glob(f"fet-bridge-{v}-*MB.bin"))
    if not images:
        sys.exit(f"no dist/firmware/fet-bridge-{v}-*MB.bin: export from Arduino and run tools/collect_firmware.py first")
    folder = ROOT / "dist" / f"fw-{v}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for img in images:
        try:
            meta = inspect(img.read_bytes())
        except BadImage as exc:
            sys.exit(f"{img.name}: {exc}")
        if meta["version"] != v:
            sys.exit(f"{img.name} says it is {meta['version']}")
        shutil.copyfile(img, folder / img.name)
    return folder, [i.name for i in images], f"fw-{v}", f"FeT Bridge firmware {v}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=["server", "firmware"])
    ap.add_argument("version", nargs="?", help="firmware version (firmware only)")
    ap.add_argument("--notes", help="release notes (Markdown); shown in Range Control before updating")
    ap.add_argument("--key", default=str(KEY))
    ap.add_argument("--publish", action="store_true", help="run gh release create (asks first)")
    a = ap.parse_args()
    try:
        folder, names, tag, title = (server if a.kind == "server" else firmware)(a)
        sums = sums_for(folder, names)
        sign(sums, Path(a.key))
        assert set(parse_sums(sums.read_bytes())) == set(names)
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        print(f"not released: {exc}", file=sys.stderr)
        return 1
    files = [str(folder / n) for n in names] + [str(sums), str(sums.with_name("SHA256SUMS.sig"))]
    cmd = ["gh", "release", "create", tag, *files, "--title", title]
    cmd += ["--notes-file", a.notes] if a.notes else ["--notes", title]
    print("\nReady in", folder)
    print("Publish with:\n  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if a.publish:
        if input(f"\nPublish {tag} to GitHub now? Type yes: ").strip() != "yes":
            print("Not published.")
            return 0
        subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
