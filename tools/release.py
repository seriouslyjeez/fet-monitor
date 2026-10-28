#!/usr/bin/env python3
"""Build, sign and (optionally) publish a release.

    python3 tools/release.py server [--notes NOTES.md] [--publish]
    python3 tools/release.py firmware 0.2.2 [--notes NOTES.md] [--publish]
    python3 tools/release.py image 0.6.30 [--publish]

server   : the server archive from the committed tree (tools/make_release.py) -> dist/server-<ver>/
firmware : the collected images dist/firmware/fet-bridge-<ver>-*.bin (tools/collect_firmware.py) -> dist/fw-<ver>/
image    : the SD card image GitHub Actions built for server release v<ver> (.github/workflows/image.yml): downloads it
           and its IMAGE-SHA256SUMS, checks the hash here, signs -> IMAGE-SHA256SUMS.sig, uploads that (with --publish)

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
    sig = sums.with_name(sums.name + ".sig")
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


def image(a) -> int:
    """Sign the image the workflow attached to release v<version>, after checking its hash on this computer."""
    if not a.version or not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        sys.exit("image: give the server version, e.g. 0.6.30")
    tag, name = f"v{a.version}", f"fet-monitor-{a.version}-pi.img.xz"
    folder = ROOT / "dist" / f"image-{a.version}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    print(f"Downloading {name} and IMAGE-SHA256SUMS from {tag} (the image is large)...")
    subprocess.run(["gh", "release", "download", tag, "-p", name, "-p", "IMAGE-SHA256SUMS", "-D", str(folder)], check=True)
    sums = folder / "IMAGE-SHA256SUMS"
    listed = parse_sums(sums.read_bytes())
    if set(listed) != {name}:
        sys.exit(f"IMAGE-SHA256SUMS should list exactly {name}; it lists {sorted(listed)}")
    h = hashlib.sha256()
    with open(folder / name, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != listed[name]:
        sys.exit(f"{name}: its SHA-256 here does not match IMAGE-SHA256SUMS: not signed")
    sign(sums, Path(a.key))
    cmd = ["gh", "release", "upload", tag, str(sums.with_name(sums.name + ".sig")), "--clobber"]
    print("\nChecked and signed. Upload with:\n  " + " ".join(cmd))
    if a.publish and input(f"\nUpload the image signature to {tag} now? Type yes: ").strip() == "yes":
        subprocess.run(cmd, check=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=["server", "firmware", "image"])
    ap.add_argument("version", nargs="?", help="firmware version (firmware), server version (image)")
    ap.add_argument("--notes", help="release notes (Markdown); shown in Range Control before updating")
    ap.add_argument("--key", default=str(KEY))
    ap.add_argument("--publish", action="store_true", help="run gh release create (asks first)")
    a = ap.parse_args()
    if a.kind == "image":
        try:
            return image(a)
        except (ReleaseError, subprocess.CalledProcessError) as exc:
            print(f"not signed: {exc}", file=sys.stderr)
            return 1
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
