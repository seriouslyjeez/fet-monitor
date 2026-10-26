"""Signed releases: a local fake GitHub serves releases signed with a throwaway key; every way a release can be wrong
must be refused. Needs ssh-keygen (skipped otherwise)."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fetserver import releases
from fetserver.firmware import FirmwareStore
from fetserver.releases import GitHub, ReleaseError, Updates, check_archive, fetch_firmware, server_update_info, stage_server_update
from tests.test_firmware import image

REPO = "someone/fet-monitor"
HAVE_SSH = shutil.which("ssh-keygen") is not None


def make_key(d: Path, name="k"):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(d / name), "-C", "test"], check=True)
    pub = (d / f"{name}.pub").read_text().split()
    signers = d / f"{name}.signers"
    signers.write_text(f'release@fet-monitor namespaces="fet-monitor-release" {pub[0]} {pub[1]}\n')
    return d / name, signers


def sign(key: Path, data: bytes, d: Path) -> bytes:
    p = d / "SUMS"
    p.write_bytes(data)
    if (d / "SUMS.sig").exists():
        (d / "SUMS.sig").unlink()
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "fet-monitor-release", str(p)], check=True, capture_output=True)
    return (d / "SUMS.sig").read_bytes()


def archive(version, members=None, claimed=None):
    """A minimal server archive; `members` adds/overrides (name -> bytes, or a TarInfo for odd types)."""
    top = f"fet-monitor-{version}"
    files = {f"{top}/install.sh": b"#!/bin/bash\n", f"{top}/fetserver/__init__.py": f'__version__ = "{claimed or version}"\n'.encode()}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in {**files, **(members or {})}.items():
            if isinstance(data, tarfile.TarInfo):
                t.addfile(data)
                continue
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class FakeGitHub:
    def __init__(self):
        self.releases = []
        self.files = {}          # path -> bytes
        self.redirects = {}      # path -> location
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == f"/repos/{REPO}/releases":
                    body = json.dumps(outer.releases).encode()
                elif path in outer.redirects:
                    self.send_response(302)
                    self.send_header("Location", outer.redirects[path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                elif path in outer.files:
                    body = outer.files[path]
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def add(self, tag, files: dict, draft=False, prerelease=False, body="notes"):
        assets = []
        for name, data in files.items():
            path = f"/{REPO}/releases/download/{tag}/{name}"
            self.files[path] = data
            assets.append({"name": name, "size": len(data), "browser_download_url": self.base + path})
        self.releases.append({"tag_name": tag, "name": tag, "body": body, "draft": draft, "prerelease": prerelease,
                              "published_at": "2026-10-26T10:00:00Z", "html_url": "https://github.com/x", "assets": assets})

    def gh(self):
        return GitHub(api=self.base, repo=REPO, allowed_hosts={"127.0.0.1"}, insecure_for_tests=True, timeout=5)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ReleasesBase(unittest.TestCase):
    """Throwaway keys, a fake GitHub and helpers; no tests of its own."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.key, self.signers = make_key(self.d)
        self.other_key, _ = make_key(self.d, "other")
        self.fake = FakeGitHub()
        self.addCleanup(self.fake.close)

    def release(self, tag, files: dict, key=None, sums_extra=b"", **kw):
        sums = b"".join(f"{hashlib.sha256(v).hexdigest()}  {k}\n".encode() for k, v in files.items()) + sums_extra
        self.fake.add(tag, {**files, "SHA256SUMS": sums, "SHA256SUMS.sig": sign(key or self.key, sums, self.d)}, **kw)



@unittest.skipUnless(HAVE_SSH, "ssh-keygen not available")
class Releases(ReleasesBase):
    def test_newest_server_release_and_notes(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30")})
        self.release("v0.6.100", {"fet-monitor-0.6.100.tar.gz": archive("0.6.100")}, body="Big <b>changes</b>")
        self.release("v0.7.0", {"fet-monitor-0.7.0.tar.gz": archive("0.7.0")}, prerelease=True)
        self.release("v0.8.0", {"fet-monitor-0.8.0.tar.gz": archive("0.8.0")}, draft=True)
        self.release("fw-9.9.9", {"x": b"y"})
        info = server_update_info(self.fake.gh(), "0.6.23")
        self.assertEqual((info["available"], info["latest"]["version"], info["latest"]["notes"]), (True, "0.6.100", "Big <b>changes</b>"))
        self.assertFalse(server_update_info(self.fake.gh(), "0.6.100")["available"])
        self.assertFalse(server_update_info(self.fake.gh(), "1.0.0")["available"], "never offers a downgrade")

    def test_stage_good_release(self):
        data = archive("0.6.30")
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": data})
        stage = self.d / "stage"
        meta = stage_server_update(self.fake.gh(), "0.6.30", stage, self.signers)
        self.assertEqual(meta["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(sorted(os.listdir(stage)), ["SHA256SUMS", "SHA256SUMS.sig", "fet-monitor-0.6.30.tar.gz"])

    def refused(self, why, version="0.6.30", signers=None):
        stage = self.d / ("stage-" + why.replace(" ", "_")[:20])
        with self.assertRaises(ReleaseError) as cm:
            stage_server_update(self.fake.gh(), version, stage, signers or self.signers)
        self.assertIn(why, str(cm.exception))
        self.assertFalse(stage.exists() and any(stage.iterdir()), "nothing staged for a refused release")

    def test_signed_by_another_key(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30")}, key=self.other_key)
        self.refused("signature is NOT valid")

    def test_archive_swapped_after_signing(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30")})
        self.fake.files[f"/{REPO}/releases/download/v0.6.30/fet-monitor-0.6.30.tar.gz"] = archive("0.6.30", {"fet-monitor-0.6.30/evil.py": b"x"})
        self.refused("does not match the signed checksum")

    def test_sums_changed_after_signing(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30")})
        p = f"/{REPO}/releases/download/v0.6.30/SHA256SUMS"
        self.fake.files[p] = self.fake.files[p].replace(b"  fet", b"  fet") + b"0" * 64 + b"  other\n"
        self.refused("signature is NOT valid")

    def test_missing_signature(self):
        data = archive("0.6.30")
        sums = f"{hashlib.sha256(data).hexdigest()}  fet-monitor-0.6.30.tar.gz\n".encode()
        self.fake.add("v0.6.30", {"fet-monitor-0.6.30.tar.gz": data, "SHA256SUMS": sums})
        self.refused("has no SHA256SUMS.sig")

    def test_signed_but_unsafe_archives(self):
        evil = tarfile.TarInfo("fet-monitor-0.6.30/link")
        evil.type, evil.linkname = tarfile.SYMTYPE, "/etc/passwd"
        for i, (members, why) in enumerate([({"fet-monitor-0.6.30/../../etc/x": b"x"}, "outside"),
                                            ({"other-top/x": b"x"}, "outside"),
                                            ({"/abs": b"x"}, "outside"),
                                            ({"x": evil}, "not a plain file")]):
            v = f"0.6.{40 + i}"
            ms = {k.replace("0.6.30", v): val for k, val in members.items()}
            if isinstance(members.get("x"), tarfile.TarInfo):
                evil.name = f"fet-monitor-{v}/link"
            self.release(f"v{v}", {f"fet-monitor-{v}.tar.gz": archive(v, ms)})
            self.refused(why, v)

    def test_version_inside_must_match(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30", claimed="0.6.99")})
        self.refused("version does not match")

    def test_only_github_hosts(self):
        data = archive("0.6.30")
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": data})
        p = f"/{REPO}/releases/download/v0.6.30/fet-monitor-0.6.30.tar.gz"
        self.fake.redirects[p] = self.fake.base.replace("127.0.0.1", "localhost") + p     # a host not on the list
        self.refused("not GitHub over HTTPS")
        gh = GitHub(api=self.fake.base, repo=REPO, allowed_hosts={"127.0.0.1"})           # an allowed host, but plain HTTP
        with self.assertRaises(ReleaseError) as cm:
            gh.releases()
        self.assertIn("not GitHub over HTTPS", str(cm.exception))

    def test_size_caps(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30", {"fet-monitor-0.6.30/big": os.urandom(300_000)})})
        old = releases.MAX_ARCHIVE
        releases.MAX_ARCHIVE = 100_000
        try:
            self.refused("allowed")
        finally:
            releases.MAX_ARCHIVE = old

    def test_check_archive_rejects_garbage(self):
        for data in (b"", b"not a tar", archive("1.0.0")[:50]):
            with self.assertRaises(ReleaseError):
                check_archive(data, "1.0.0")

    # ---- firmware releases
    def test_fetch_firmware(self):
        one, four = image(version="0.2.5", flash=0x20, seed=1), image(version="0.2.5", flash=0x40, seed=2)
        self.release("fw-0.2.4", {"fet-bridge-0.2.4-1MB.bin": image(version="0.2.4", seed=3)})
        self.release("fw-0.2.5", {"fet-bridge-0.2.5-1MB.bin": one, "fet-bridge-0.2.5-4MB.bin": four})
        store = FirmwareStore(self.d)
        fetch = lambda: fetch_firmware(self.fake.gh(), lambda sha: store.get(sha) is not None, lambda b: store.add(b, "GitHub"), self.signers)
        r = fetch()
        self.assertEqual((r["version"], r["added"]), ("0.2.5", ["fet-bridge-0.2.5-1MB.bin", "fet-bridge-0.2.5-4MB.bin"]))
        self.assertEqual(sorted(m["flash_size"] for m in store.list()), [1 << 20, 4 << 20])
        r = fetch()
        self.assertEqual((r["added"], len(r["already"])), ([], 2), "nothing downloaded twice")

    def test_firmware_refusals(self):
        store = FirmwareStore(self.d)
        fetch = lambda: fetch_firmware(self.fake.gh(), lambda sha: store.get(sha) is not None, lambda b: store.add(b), self.signers)
        self.release("fw-0.2.6", {"fet-bridge-0.2.6-1MB.bin": image(version="0.2.6", mode=2)})      # DOUT check still applies
        with self.assertRaises(ReleaseError) as cm:
            fetch()
        self.assertIn("DOUT", str(cm.exception))
        self.release("fw-0.2.7", {"fet-bridge-0.2.7-1MB.bin": image(version="0.2.6")})              # claims 0.2.7, is 0.2.6
        with self.assertRaises(ReleaseError):
            fetch()
        self.release("fw-0.2.8", {"fet-bridge-0.2.8-1MB.bin": image(version="0.2.8")}, key=self.other_key)
        with self.assertRaises(ReleaseError) as cm:
            fetch()
        self.assertIn("NOT valid", str(cm.exception))
        self.assertEqual(store.list(), [])

    # ---- the server's side
    def test_updates_install_writes_the_request(self):
        self.release("v0.6.30", {"fet-monitor-0.6.30.tar.gz": archive("0.6.30")})
        helper = self.d / "helper"
        up = Updates(self.d, self.fake.gh(), self.signers, helper=helper)
        with self.assertRaises(ReleaseError) as cm:
            up.install("0.6.30")
        self.assertIn("helper is not installed", str(cm.exception))
        helper.write_text("#!/bin/sh\n")
        meta = up.install("0.6.30")
        self.assertEqual(json.loads((self.d / "update/request.json").read_text())["version"], "0.6.30")
        self.assertEqual(meta["archive"], "fet-monitor-0.6.30.tar.gz")
        with self.assertRaises(ReleaseError) as cm:
            up.install("0.6.30")
        self.assertIn("already in progress", str(cm.exception))
        self.assertTrue(up.status()["pending"])
        self.assertEqual(up.check("0.6.23")["last_check"]["latest"]["version"], "0.6.30")

    def test_real_signers_file_is_well_formed(self):
        line = [ln for ln in releases.SIGNERS.read_text().splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(len(line), 1)
        self.assertTrue(line[0].startswith('release@fet-monitor namespaces="fet-monitor-release" ssh-ed25519 '))


@unittest.skipUnless(HAVE_SSH, "ssh-keygen not available")
class UpdatesHttp(ReleasesBase):
    def setUp(self):
        super().setUp()
        from fetserver.admin import Admin, write_password
        from fetserver.core import Core
        from fetserver.db import Database
        from fetserver.server import make_server
        self.helper = self.d / "helper"
        self.helper.write_text("#!/bin/sh\n")
        self.updates = Updates(self.d, self.fake.gh(), self.signers, helper=self.helper)
        self.store = FirmwareStore(self.d)
        self.server = make_server(Core(Database(str(self.d / "t.db"))), "tok", "127.0.0.1", 0, admin=Admin(self.d),
                                  firmware=self.store, updates=self.updates)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        write_password(self.d, "range-pass")
        self.cookie = None
        st, _, hdr = self.call("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookie = hdr["Set-Cookie"].split(";")[0]

    def call(self, method, path, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=20)
        h = {"X-FET-Admin": "1", "Content-Type": "application/json"}
        if self.cookie:
            h["Cookie"] = self.cookie
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"), dict(r.getheaders()))
        c.close()
        return out

    def test_check_install_and_firmware_over_http(self):
        from fetserver import __version__
        nxt = "9.0.0"
        self.release(f"v{nxt}", {f"fet-monitor-{nxt}.tar.gz": archive(nxt)}, body="What changed")
        self.release("fw-0.2.9", {"fet-bridge-0.2.9-1MB.bin": image(version="0.2.9")})
        st, body, _ = self.call("GET", "/api/v1/admin/updates")
        self.assertEqual((st, body["current"], body["last_check"]), (200, __version__, None), "GET never contacts GitHub")
        st, body, _ = self.call("POST", "/api/v1/admin/updates/check", {})
        self.assertEqual((st, body["last_check"]["latest"]["version"], body["last_check"]["latest"]["notes"]), (200, nxt, "What changed"))
        st, body, _ = self.call("POST", "/api/v1/admin/updates/install", {"version": nxt})
        self.assertEqual((st, body["version"]), (202, nxt))
        self.assertTrue(self.call("GET", "/api/v1/admin/updates")[1]["pending"])
        self.assertEqual(self.call("POST", "/api/v1/admin/updates/install", {"version": nxt})[0], 400, "one at a time")
        st, body, _ = self.call("POST", "/api/v1/admin/firmware/github", {})
        self.assertEqual((st, body["added"]), (200, ["fet-bridge-0.2.9-1MB.bin"]))
        self.assertEqual([m["uploaded_by"] for m in self.store.list()], ["GitHub"])
        self.cookie = None
        for path in ("/api/v1/admin/updates/check", "/api/v1/admin/updates/install", "/api/v1/admin/firmware/github"):
            self.assertEqual(self.call("POST", path, {"version": nxt})[0], 401, path)

    def test_github_unreachable(self):
        self.updates.gh = GitHub(api="https://api.github.com", repo=REPO, allowed_hosts={"api.github.com"}, timeout=2)
        self.updates.gh.api = "http://127.0.0.1:9"                 # plain HTTP: refused before any connection
        st, body, _ = self.call("POST", "/api/v1/admin/updates/check", {})
        self.assertEqual(st, 502)
        self.assertIn("not GitHub over HTTPS", body["error"])


if __name__ == "__main__":
    unittest.main()
