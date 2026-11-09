#!/usr/bin/env python3
"""P3 step 1: can the 4 MB ESP-01S do HTTPS well enough for direct targets? (docs/PLAN-deeptens.md)

    python3 tools/tls_test.py certs --ip 192.168.50.101   # a test CA + a certificate for 192-168-50-101.sslip.io
    python3 tools/tls_test.py build                       # FeT Bridge HTTPS test images, 80 and 160 MHz (4 MB)
    python3 tools/tls_test.py proxy --to http://127.0.0.1:8090   # HTTPS on :8443 in front of a FeT Monitor, logging
    python3 tools/tls_test.py report                      # what the log says

The test CA is ECDSA P-256, like the planned deepTENS device CA; its key never leaves dist/tls-test/ (git ignores
dist/). The server's name is <ip with dashes>.sslip.io, a public DNS name that answers with the IP in it, so the bridge
looks the name up, sends it (SNI) and checks it against the certificate exactly as it would for devices.deeptens.com.

The proxy keeps connections open (HTTP/1.1 keep-alive) like Caddy would, closes one after IDLE seconds without a
request (so reconnecting is exercised too), forwards each request to the FeT Monitor unchanged and writes one CSV line
per request: the connection, its TLS handshake as the server saw it (time, version, cipher, resumed), and the bridge's
own measurements from its X-FET-TLS header. Needs a Python with a recent OpenSSL (Homebrew python3).
"""
from __future__ import annotations

import argparse
import csv
import http.client
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "tls-test"
HEADER = ROOT / "firmware" / "fet_bridge" / "src" / "platform" / "tls_test_ca.h"
SKETCH = ROOT / "firmware" / "fet_bridge"
FQBN = "esp8266:esp8266:generic:eesz=4M2M,FlashMode=dout,xtal={mhz},baud=115200"
FIELDS = ["time", "conn", "req_on_conn", "server_hs_ms", "tls", "cipher", "resumed", "path", "status", "backend_ms",
          "c", "r", "new", "hs", "hsmax", "hsavg", "heap", "heap0", "minheap", "blk", "minblk", "frag", "fails", "retries",
          "err", "mfln", "mhz", "rssi", "up"]


def run(*cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"{' '.join(cmd[:3])}...: {r.stderr.strip()[-800:]}")
    return r.stdout


def host_of(ip: str) -> str:
    return ip.replace(".", "-") + ".sslip.io"


def certs(args) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    name = host_of(args.ip)
    ca_key, ca_crt, key, csr, crt = (OUT / f for f in ("ca.key", "ca.crt", "server.key", "server.csr", "server.crt"))
    ossl = next((p for p in ("/opt/homebrew/opt/openssl/bin/openssl", "/usr/local/opt/openssl/bin/openssl") if os.path.exists(p)), None) \
        or shutil.which("openssl") or "openssl"
    run(ossl, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(ca_key))
    # A CA certificate as strict checkers (OpenSSL 3 with X509_STRICT, Python 3.13+) want it: CA:TRUE, key usage, and
    # the key identifiers (added by OpenSSL 3 for -x509) that tie the server certificate to it. The real device CA too.
    run(ossl, "req", "-x509", "-new", "-key", str(ca_key), "-sha256", "-days", "365", "-subj", "/CN=FeT TLS test CA",
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash", "-out", str(ca_crt))
    run(ossl, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key))
    run(ossl, "req", "-new", "-key", str(key), "-subj", f"/CN={name}", "-out", str(csr))
    with tempfile.NamedTemporaryFile("w", suffix=".ext", delete=False) as ext:
        ext.write(f"subjectAltName=DNS:{name}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                  "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
    run(ossl, "x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial", "-days", "90",
        "-sha256", "-extfile", ext.name, "-out", str(crt))
    os.unlink(ext.name)
    for f in (ca_key, key):
        os.chmod(f, 0o600)
    pem = ca_crt.read_text().strip()
    HEADER.write_text("// The HTTPS test CA (tools/tls_test.py certs). Generated, not in git: a test certificate only.\n#pragma once\n"
                      f"static const char FET_TLS_TEST_CA[] = R\"PEM({pem}\n)PEM\";\n")
    print(f"test CA and a certificate for {name} in {OUT}\nCA certificate for the firmware: {HEADER}\n"
          f"Set the bridge's server to {name}, port 8443 (and the FeT Monitor's device token).")


def build(args) -> None:
    if not HEADER.exists():
        sys.exit("make the certificates first: tls_test.py certs --ip <this Mac's address>")
    OUT.mkdir(parents=True, exist_ok=True)
    flags = "-DFET_TLS_TEST=1"
    for mhz in args.mhz:
        with tempfile.TemporaryDirectory() as tmp:
            print(f"== building the HTTPS test image, {mhz} MHz, 4 MB ...", flush=True)
            r = subprocess.run(["arduino-cli", "compile", "-b", FQBN.format(mhz=mhz), "--output-dir", tmp,
                                "--build-property", f"compiler.cpp.extra_flags={flags}", "--build-property", f"compiler.c.extra_flags={flags}",
                                str(SKETCH)], capture_output=True, text=True)
            if r.returncode:
                sys.exit(r.stdout[-3000:] + r.stderr[-3000:])
            for line in r.stdout.splitlines():
                if "Sketch uses" in line or "Global variables" in line:
                    print("  " + line.strip())
            dst = OUT / f"fet-bridge-0.4.1-tls{mhz}-4MB.bin"
            shutil.copy(Path(tmp) / "fet_bridge.ino.bin", dst)
            print(f"  {dst} ({dst.stat().st_size} bytes)")


class Proxy:
    def __init__(self, to: str, port: int, idle: float, log: Path):
        u = urlparse(to)
        self.backend = (u.hostname, u.port or 80)
        self.port, self.idle, self.log = port, idle, log
        self.lock = threading.Lock()
        self.conns = 0
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(OUT / "server.crt", OUT / "server.key")
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        new = not log.exists()
        self.fh = open(log, "a", newline="")
        self.w = csv.DictWriter(self.fh, FIELDS)
        if new:
            self.w.writeheader()

    def serve(self) -> None:
        s = socket.create_server(("0.0.0.0", self.port), reuse_port=False)
        print(f"HTTPS on :{self.port} -> {self.backend[0]}:{self.backend[1]}; idle connections closed after {self.idle:.0f} s; log {self.log}", flush=True)
        while True:
            raw, addr = s.accept()
            threading.Thread(target=self.handle, args=(raw, addr), daemon=True).start()

    def handle(self, raw, addr) -> None:
        with self.lock:
            self.conns += 1
            cid = self.conns
        raw.settimeout(15)
        t0 = time.monotonic()
        try:
            tls = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            print(f"[{cid}] {addr[0]} handshake failed: {exc}", flush=True)
            raw.close()
            return
        hs = round((time.monotonic() - t0) * 1000)
        info = {"conn": cid, "server_hs_ms": hs, "tls": tls.version(), "cipher": (tls.cipher() or ("",))[0], "resumed": int(tls.session_reused)}
        print(f"[{cid}] {addr[0]} {info['tls']} {info['cipher']} handshake {hs} ms{' (resumed)' if info['resumed'] else ''}", flush=True)
        f = tls.makefile("rb")
        n = 0
        try:
            tls.settimeout(self.idle)
            while True:
                line = f.readline(8192)
                if not line:
                    break
                n += 1
                method, path, _ = line.decode("latin-1").split(" ", 2)
                headers = {}
                while True:
                    h = f.readline(8192).decode("latin-1")
                    if h in ("\r\n", "\n", ""):
                        break
                    k, _, v = h.partition(":")
                    headers[k.strip().lower()] = v.strip()
                body = f.read(int(headers.get("content-length", "0") or 0))
                b0 = time.monotonic()
                c = http.client.HTTPConnection(*self.backend, timeout=10)
                fwd = {k: v for k, v in headers.items() if k not in ("connection", "host", "content-length")}
                c.request(method, path, body=body, headers=fwd)
                r = c.getresponse()
                data = r.read()
                c.close()
                bms = round((time.monotonic() - b0) * 1000)
                tls.sendall(f"HTTP/1.1 {r.status} {r.reason}\r\nContent-Type: {r.getheader('Content-Type', 'application/json')}\r\n"
                            f"Content-Length: {len(data)}\r\nConnection: keep-alive\r\n\r\n".encode() + data)
                row = dict(info, time=time.strftime("%H:%M:%S"), req_on_conn=n, path=path, status=r.status, backend_ms=bms)
                for part in headers.get("x-fet-tls", "").split(";"):
                    k, _, v = part.partition("=")
                    if k in FIELDS:
                        row[k] = v
                with self.lock:
                    self.w.writerow(row)
                    self.fh.flush()
        except (socket.timeout, TimeoutError):
            print(f"[{cid}] idle {self.idle:.0f} s after {n} requests: closed", flush=True)
        except (ssl.SSLError, OSError, ValueError) as exc:
            print(f"[{cid}] closed after {n} requests: {exc}", flush=True)
        finally:
            try:
                tls.close()
            except OSError:
                pass


def proxy(args) -> None:
    if not (OUT / "server.crt").exists():
        sys.exit("make the certificates first: tls_test.py certs --ip <this Mac's address>")
    Proxy(args.to, args.port, args.idle, Path(args.log)).serve()


def report(args) -> None:
    rows = list(csv.DictReader(open(args.log)))
    if not rows:
        sys.exit("nothing logged yet")

    def nums(k, sel=lambda r: True):
        return sorted(int(r[k]) for r in rows if sel(r) and (r.get(k) or "").lstrip("-").isdigit())

    def summary(v):
        return f"median {v[len(v) // 2]}, max {v[-1]} (n={len(v)})" if v else "none"
    by_mhz = sorted({r.get("mhz") for r in rows if r.get("mhz")})
    for mhz in by_mhz:
        sel = lambda r, m=mhz: r.get("mhz") == m
        new = lambda r, m=mhz: r.get("mhz") == m and r.get("new") == "1"
        print(f"== {mhz} MHz: {sum(1 for r in rows if sel(r))} requests on {len({r['conn'] for r in rows if sel(r)})} connections")
        print(f"  handshake on the ESP (ms): {summary(nums('hs', new))}")
        print(f"  handshake seen by the server (ms): {summary(nums('server_hs_ms', new))}")
        print(f"  free heap now / least ever (bytes): {summary(nums('heap', sel))} / least {min(nums('minheap', sel) or [0])}")
        print(f"  largest free block, least ever: {min(nums('minblk', sel) or [0])}; fragmentation max {max(nums('frag', sel) or [0])} %")
        last = [r for r in rows if sel(r)][-1]
        print(f"  failures {last.get('fails')}, reconnects after the server closed {last.get('retries')}, last error {last.get('err')}, "
              f"MFLN {'yes' if last.get('mfln') == '1' else 'no'}, up {last.get('up')} s, rssi {last.get('rssi')}")
        print(f"  TLS {sorted({r['tls'] for r in rows if sel(r)})} {sorted({r['cipher'] for r in rows if sel(r)})}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("certs")
    c.add_argument("--ip", required=True, help="this Mac's address on the bench network")
    b = sp.add_parser("build")
    b.add_argument("--mhz", nargs="+", default=["80", "160"], choices=["80", "160"])
    p = sp.add_parser("proxy")
    p.add_argument("--to", default="http://127.0.0.1:8090", help="the FeT Monitor to forward to")
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--idle", type=float, default=60, help="close a connection after this many seconds without a request")
    p.add_argument("--log", default=str(OUT / "log.csv"))
    r = sp.add_parser("report")
    r.add_argument("--log", default=str(OUT / "log.csv"))
    a = ap.parse_args()
    {"certs": certs, "build": build, "proxy": proxy, "report": report}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
