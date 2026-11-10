#!/usr/bin/env python3
"""The deepTENS device CA (P3 step 2; docs/PLAN-deeptens.md, user 2026-11-09): the certificate authority whose root
the targets' firmware trusts for devices.deeptens.com.

    python3 tools/device_ca.py init        # once: the root key (passphrase-encrypted) and certificate, 20 years
    python3 tools/device_ca.py sign devices.csr            # the server's certificate (2 years), from its request
    python3 tools/device_ca.py show                        # the root and when things expire
    python3 tools/device_ca.py header                      # (again) the firmware's copy of the root certificate

The root key lives only on this Mac, in ~/.deeptens-device-ca/ (folder 700, key 600, AES-256 with a passphrase that
is never stored): back that folder up yourself, like the release signing key. Losing it means a firmware update for
every HTTPS target to trust a new root; a stolen key AND passphrase would let someone impersonate devices.deeptens.com
to targets, so keep both safe. The server's key is made on the server (ops/device-cert.sh request) and never travels:
only its request (CSR) comes here and its certificate goes back.

ECDSA P-256 throughout (what the bridge measured in step 1). The certificates carry exactly what strict TLS checkers
and BearSSL want: CA:TRUE with pathlen 0 and key usage on the root, the server's name in subjectAltName, serverAuth,
and the key identifiers. No name constraints (BearSSL rejects critical extensions it does not know).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CA_DIR = Path(os.environ.get("DEEPTENS_DEVICE_CA", Path.home() / ".deeptens-device-ca"))
KEY, CERT = CA_DIR / "root.key", CA_DIR / "root.crt"
PUBLIC = ROOT / "docs" / "deeptens-device-ca.pem"             # the root certificate is public: kept in git
HEADER = ROOT / "firmware" / "fet_bridge" / "src" / "core" / "device_ca.h"   # shared: FeT Bridge and the ESP32 target
ROOT_DAYS, SERVER_DAYS = 7305, 730                            # 20 years; 2 years (renewed ~60 days before)
NAMES = ("devices.deeptens.com", "devices.staging.deeptens.com")


def openssl() -> str:
    for p in ("/opt/homebrew/opt/openssl/bin/openssl", "/usr/local/opt/openssl/bin/openssl"):
        if os.path.exists(p):
            return p
    sys.exit("needs OpenSSL 3 or newer (brew install openssl); macOS's own LibreSSL makes certificates strict checkers refuse")


PASS_ENV = "DEVICE_CA_TEST_PASS"     # tests only: the passphrase from this variable instead of the terminal


def pw(flag: str) -> list:
    return [flag, "env:" + PASS_ENV] if os.environ.get(PASS_ENV) else []


def run(*cmd, interactive=False) -> str:
    r = subprocess.run(cmd, text=True, capture_output=not interactive)
    if r.returncode:
        sys.exit(f"{cmd[1]} failed" + ("" if interactive else f": {r.stderr.strip()[-600:]}"))
    return "" if interactive else r.stdout


def write_header() -> None:
    pem = CERT.read_text().strip() if CERT.exists() else PUBLIC.read_text().strip()
    PUBLIC.write_text(pem + "\n")
    HEADER.write_text("// The deepTENS device CA's root certificate (tools/device_ca.py). Public; the targets trust it for\n"
                      "// devices.deeptens.com. Its key never leaves the owner's Mac.\n#pragma once\n"
                      f"static const char FET_DEVICE_CA[] = R\"PEM({pem}\n)PEM\";\n")
    print(f"root certificate: {PUBLIC}\nfirmware copy: {HEADER}")


def init(args) -> None:
    if KEY.exists() or CERT.exists():
        sys.exit(f"{CA_DIR} already holds a root: refusing to replace it (that would need a firmware update everywhere)")
    o = openssl()
    CA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CA_DIR, 0o700)
    print("Choose a passphrase for the device CA's root key (asked twice, then again to sign the root certificate).")
    run(o, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-aes-256-cbc", *pw("-pass"), "-out", str(KEY), interactive=True)
    os.chmod(KEY, 0o600)
    run(o, "req", "-x509", "-new", "-key", str(KEY), "-sha256", "-days", str(ROOT_DAYS), "-subj", "/O=deepTENS/CN=deepTENS Device Root CA",
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash", *pw("-passin"), "-out", str(CERT), interactive=True)
    write_header()
    print(f"\nDone. Back up {CA_DIR} now (it holds the only copy of the root key), and remember the passphrase.")
    show(args)


def sign(args) -> None:
    if not KEY.exists():
        sys.exit("no root yet: python3 tools/device_ca.py init")
    o = openssl()
    csr = Path(args.csr)
    subj = run(o, "req", "-in", str(csr), "-noout", "-subject", "-verify").strip()
    name = args.name
    if name not in NAMES:
        sys.exit(f"--name: one of {', '.join(NAMES)}")
    out = Path(args.out or csr.with_suffix(".crt"))
    with tempfile.NamedTemporaryFile("w", suffix=".ext", delete=False) as ext:
        ext.write(f"subjectAltName=DNS:{name}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                  "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
    print(f"signing {csr} ({subj}) for {name}, {SERVER_DAYS} days: the root key's passphrase, please")
    try:
        run(o, "x509", "-req", "-in", str(csr), "-CA", str(CERT), "-CAkey", str(KEY), "-CAcreateserial",
            "-CAserial", str(CA_DIR / "root.srl"), "-days", str(SERVER_DAYS), "-sha256", "-extfile", ext.name, *pw("-passin"), "-out", str(out), interactive=True)
    finally:
        os.unlink(ext.name)
    run(o, "verify", "-x509_strict", "-CAfile", str(CERT), str(out))
    print(f"{out}: verified against the root. Copy it to the server: ops/device-cert.sh install {out.name}")
    print(run(o, "x509", "-in", str(out), "-noout", "-subject", "-enddate").strip())


def show(args) -> None:
    o = openssl()
    pem = CERT if CERT.exists() else PUBLIC
    if not pem.exists():
        sys.exit("no root yet: python3 tools/device_ca.py init")
    print(run(o, "x509", "-in", str(pem), "-noout", "-subject", "-enddate", "-fingerprint", "-sha256").strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    s = sp.add_parser("sign")
    s.add_argument("csr")
    s.add_argument("--name", default="devices.deeptens.com")
    s.add_argument("--out")
    sp.add_parser("show")
    sp.add_parser("header")
    a = ap.parse_args()
    {"init": init, "sign": sign, "show": show, "header": lambda _: write_header()}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
