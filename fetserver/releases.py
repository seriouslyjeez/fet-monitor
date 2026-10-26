"""Signed releases from GitHub: FeT Monitor server updates and FeT Bridge firmware.

Trust model. A release is used only if its SHA256SUMS file carries a valid `ssh-keygen -Y` signature (namespace
SIGN_NAMESPACE, identity SIGN_IDENTITY) by a key in fetserver/release_signers, and every file used is listed in that
SHA256SUMS with a matching hash. GitHub only transports the files: a changed release, or a hijacked account without the
private key, is refused. The signers file is part of the installed code, so only a release signed by a key that is
already trusted can change it.

Being particular about what we download:
* HTTPS only, and only to GitHub's own hosts (also after redirects); one fixed repository.
* Every response has a size cap; downloads are streamed and cut off past it.
* Only assets with exact expected names are fetched: server releases are tagged v<x.y.z> with fet-monitor-<x.y.z>.tar.gz,
  firmware releases fw-<x.y.z> with fet-bridge-<x.y.z>-<N>MB.bin; both with SHA256SUMS and SHA256SUMS.sig.
* A server archive is unpacked only after its checks, and only if every member is a plain file or folder inside
  fet-monitor-<x.y.z>/ (no links, devices, absolute paths or ..), and its fetserver/__init__.py names the same version.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from .firmware import BadImage, inspect, version_tuple

REPO = "seriouslyjeez/fet-monitor"
API = "https://api.github.com"
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
SIGNERS = Path(__file__).resolve().parent / "release_signers"
SIGN_NAMESPACE = "fet-monitor-release"
SIGN_IDENTITY = "release@fet-monitor"
MAX_LIST = 2 * 1024 * 1024
MAX_SUMS = 16 * 1024
MAX_SIG = 16 * 1024
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_FIRMWARE = 1024 * 1024
MAX_UNPACKED = 32 * 1024 * 1024
CACHE_S = 300
SERVER_TAG = re.compile(r"^v(\d{1,3}\.\d{1,3}\.\d{1,3})$")
FW_TAG = re.compile(r"^fw-(\d{1,3}\.\d{1,3}\.\d{1,3})$")
FW_ASSET = re.compile(r"^fet-bridge-(\d{1,3}\.\d{1,3}\.\d{1,3})-(1|2|4|8|16)MB\.bin$")
SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *]([A-Za-z0-9._-]{1,100})$")


class ReleaseError(Exception):
    """Something about a release is not as it must be; the message says what (shown in Range Control)."""


# --------------------------------------------------------------------------------------------- transport
class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check):
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, api: str = API, repo: str = REPO, allowed_hosts=ALLOWED_HOSTS, insecure_for_tests: bool = False,
                 timeout: float = 20.0):
        self.api, self.repo, self.allowed, self.insecure, self.timeout = api.rstrip("/"), repo, set(allowed_hosts), insecure_for_tests, timeout
        self.opener = urllib.request.build_opener(_Redirects(self._check_url))
        self._cache: Tuple[float, Optional[list]] = (0.0, None)

    def _check_url(self, url: str) -> None:
        u = urlparse(url)
        scheme_ok = u.scheme == "https" or (self.insecure and u.scheme == "http")
        if not scheme_ok or (u.hostname or "") not in self.allowed:
            raise ReleaseError(f"refusing to download from {u.scheme}://{u.hostname}: not GitHub over HTTPS")

    def get(self, url: str, limit: int, accept: str = "application/octet-stream") -> bytes:
        self._check_url(url)
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "fet-monitor-updater"})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                self._check_url(r.geturl())
                declared = r.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise ReleaseError(f"{Path(urlparse(url).path).name}: {declared} bytes is more than the {limit} allowed")
                buf = io.BytesIO()
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    buf.write(chunk)
                    if buf.tell() > limit:
                        raise ReleaseError(f"{Path(urlparse(url).path).name}: more than the {limit} bytes allowed")
                return buf.getvalue()
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and "/releases" in url:
                raise ReleaseError("GitHub answered 404: the repository is private or has no releases")
            if exc.code in (403, 429):
                raise ReleaseError("GitHub refused (rate limit?): try again in an hour")
            raise ReleaseError(f"GitHub answered {exc.code}")
        except urllib.error.URLError as exc:
            raise ReleaseError(f"cannot reach GitHub: {getattr(exc, 'reason', exc)}")
        except OSError as exc:                         # timeouts, resets
            raise ReleaseError(f"cannot reach GitHub: {exc}")

    def releases(self, fresh: bool = False) -> list:
        """Published, non-draft, non-prerelease releases (cached for CACHE_S: GitHub allows 60 requests an hour)."""
        at, cached = self._cache
        if cached is not None and not fresh and time.monotonic() - at < CACHE_S:
            return cached
        raw = self.get(f"{self.api}/repos/{self.repo}/releases?per_page=50", MAX_LIST, accept="application/vnd.github+json")
        try:
            data = json.loads(raw)
        except ValueError:
            raise ReleaseError("GitHub sent something that is not a release list")
        if not isinstance(data, list):
            raise ReleaseError("GitHub sent something that is not a release list")
        out = [r for r in data if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")]
        self._cache = (time.monotonic(), out)
        return out


# --------------------------------------------------------------------------------------------- checks
def verify_signature(sums: bytes, sig: bytes, signers: Path = SIGNERS) -> None:
    """Raises ReleaseError unless `sig` is a valid signature of `sums` by a trusted key."""
    if not signers.is_file():
        raise ReleaseError("no release signing key is installed (fetserver/release_signers)")
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "SHA256SUMS.sig"
        sp.write_bytes(sig)
        try:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", SIGN_IDENTITY, "-n", SIGN_NAMESPACE,
                                "-s", str(sp)], input=sums, capture_output=True, timeout=30)
        except FileNotFoundError:
            raise ReleaseError("ssh-keygen is missing (sudo apt install openssh-client)")
        except subprocess.TimeoutExpired:
            raise ReleaseError("signature check timed out")
    if r.returncode != 0:
        raise ReleaseError("the release signature is NOT valid: refusing it")


def parse_sums(sums: bytes) -> Dict[str, str]:
    out = {}
    try:
        text = sums.decode("ascii")
    except UnicodeDecodeError:
        raise ReleaseError("SHA256SUMS is not plain text")
    for line in text.splitlines():
        if not line.strip():
            continue
        m = SUMS_LINE.match(line.strip())
        if not m or m.group(2) in out:
            raise ReleaseError("SHA256SUMS has a line in an unexpected form")
        out[m.group(2)] = m.group(1)
    return out


def _asset(rel: dict, name: str) -> dict:
    for a in rel.get("assets") or []:
        if isinstance(a, dict) and a.get("name") == name:
            return a
    raise ReleaseError(f"release {rel.get('tag_name')} has no {name}")


def _download(gh: GitHub, rel: dict, name: str, limit: int) -> bytes:
    a = _asset(rel, name)
    url = a.get("browser_download_url") or ""
    prefix = f"/{gh.repo}/releases/download/"
    if not urlparse(url).path.startswith(prefix):
        raise ReleaseError(f"{name}: unexpected download address")
    if isinstance(a.get("size"), int) and a["size"] > limit:
        raise ReleaseError(f"{name}: {a['size']} bytes is more than the {limit} allowed")
    return gh.get(url, limit)


def signed_sums(gh: GitHub, rel: dict, signers: Path = SIGNERS) -> Dict[str, str]:
    sums = _download(gh, rel, "SHA256SUMS", MAX_SUMS)
    sig = _download(gh, rel, "SHA256SUMS.sig", MAX_SIG)
    verify_signature(sums, sig, signers)
    return parse_sums(sums)


def checked(gh: GitHub, rel: dict, name: str, limit: int, sums: Dict[str, str]) -> bytes:
    if name not in sums:
        raise ReleaseError(f"{name} is not in the signed SHA256SUMS")
    data = _download(gh, rel, name, limit)
    if hashlib.sha256(data).hexdigest() != sums[name]:
        raise ReleaseError(f"{name} does not match the signed checksum: refusing it")
    return data


def check_archive(data: bytes, version: str) -> None:
    """A server archive must unpack only inside fet-monitor-<version>/ and be the version it claims."""
    top = f"fet-monitor-{version}"
    total = 0
    seen = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            for m in t.getmembers():
                name = m.name
                parts = name.split("/")
                if name.startswith("/") or ".." in parts or parts[0] != top or "\\" in name:
                    raise ReleaseError(f"archive member {name!r} would land outside {top}/")
                if not (m.isfile() or m.isdir()):
                    raise ReleaseError(f"archive member {name!r} is not a plain file or folder")
                total += m.size
                if total > MAX_UNPACKED:
                    raise ReleaseError("archive unpacks to more than allowed")
                seen.add(name)
            init = t.extractfile(f"{top}/fetserver/__init__.py") if f"{top}/fetserver/__init__.py" in seen else None
            if init is None or f"{top}/install.sh" not in seen:
                raise ReleaseError("archive lacks install.sh or fetserver/")
            m = re.search(rb'__version__ = "([^"]+)"', init.read(65536))
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ReleaseError(f"archive is damaged: {exc}")
    if not m or m.group(1).decode() != version:
        raise ReleaseError("the archive's version does not match its release")


# --------------------------------------------------------------------------------------------- server updates
def newest(gh: GitHub, pattern, fresh: bool = False) -> Optional[Tuple[str, dict]]:
    best = None
    for rel in gh.releases(fresh):
        m = pattern.match(str(rel.get("tag_name", "")))
        if m and (best is None or version_tuple(m.group(1)) > version_tuple(best[0])):
            best = (m.group(1), rel)
    return best


def server_update_info(gh: GitHub, current: str, fresh: bool = False) -> dict:
    found = newest(gh, SERVER_TAG, fresh)
    if not found:
        return {"current": current, "latest": None, "available": False}
    ver, rel = found
    return {"current": current, "available": version_tuple(ver) > (version_tuple(current) or (0, 0, 0)),
            "latest": {"version": ver, "name": str(rel.get("name") or "")[:200], "notes": str(rel.get("body") or "")[:20000],
                       "published": rel.get("published_at"), "url": rel.get("html_url")}}


def stage_server_update(gh: GitHub, version: str, stage_dir: Path, signers: Path = SIGNERS) -> dict:
    """Download and check a server release into stage_dir (archive + SHA256SUMS + .sig), for the root helper, which
    checks everything again itself before installing."""
    if not version_tuple(version):
        raise ReleaseError("bad version")
    rel = next((r for r in gh.releases(fresh=True) if r.get("tag_name") == f"v{version}"), None)
    if rel is None:
        raise ReleaseError(f"no release v{version}")
    sums_raw = _download(gh, rel, "SHA256SUMS", MAX_SUMS)
    sig = _download(gh, rel, "SHA256SUMS.sig", MAX_SIG)
    verify_signature(sums_raw, sig, signers)
    name = f"fet-monitor-{version}.tar.gz"
    data = checked(gh, rel, name, MAX_ARCHIVE, parse_sums(sums_raw))
    check_archive(data, version)
    stage_dir.mkdir(parents=True, exist_ok=True)
    for fname, payload in ((name, data), ("SHA256SUMS", sums_raw), ("SHA256SUMS.sig", sig)):
        tmp = stage_dir / (fname + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, stage_dir / fname)
    return {"version": version, "archive": name, "sha256": hashlib.sha256(data).hexdigest()}


# --------------------------------------------------------------------------------------------- firmware
def fetch_firmware(gh: GitHub, have: Callable[[str], bool], add: Callable[[bytes], dict], signers: Path = SIGNERS,
                   fresh: bool = True) -> dict:
    """Download the newest signed FeT Bridge release's images that are not stored yet. Each image also has to pass
    the same checks as a manual upload."""
    found = newest(gh, FW_TAG, fresh)
    if not found:
        return {"version": None, "added": [], "already": [], "message": "no FeT Bridge firmware release on GitHub"}
    ver, rel = found
    sums = signed_sums(gh, rel, signers)
    added, already = [], []
    names = sorted(n for n in sums if FW_ASSET.match(n) and FW_ASSET.match(n).group(1) == ver)
    if not names:
        raise ReleaseError(f"release fw-{ver} lists no FeT Bridge images")
    for n in names:
        if have(sums[n]):
            already.append(n)
            continue
        data = checked(gh, rel, n, MAX_FIRMWARE, sums)
        try:
            meta = inspect(data)                       # every check BEFORE anything is stored
            if meta["version"] != ver or f"-{meta['flash_size'] >> 20}MB.bin" not in n:
                raise ReleaseError(f"{n} is really {meta['version']} for {meta['flash_size'] >> 20} MB: refusing it")
            add(data)
        except BadImage as exc:
            raise ReleaseError(f"{n}: {exc}")
        added.append(n)
    return {"version": ver, "added": added, "already": already,
            "message": (f"downloaded {', '.join(added)}" if added else f"already have every {ver} image")}



# --------------------------------------------------------------------------------------------- the server's side
class Updates:
    """Range Control's view of server updates. The server only downloads and checks; installing is done by the root
    helper (deploy/fet-monitor-update.sh), which the request file wakes up."""
    HELPER = Path(__file__).resolve().parent.parent / "bin" / "fet-monitor-update"
    STALE_MS = 15 * 60 * 1000

    def __init__(self, data_dir: Optional[Path], gh: Optional[GitHub] = None, signers: Path = SIGNERS, helper: Optional[Path] = None):
        self.dir = Path(data_dir) / "update" if data_dir is not None else None
        self.gh = gh or GitHub()
        self.signers = signers
        self.helper = helper or self.HELPER
        self.last_check: Optional[dict] = None

    def status(self) -> dict:
        st = None
        if self.dir is not None:
            try:
                st = json.loads((self.dir / "status.json").read_text())
            except (OSError, ValueError):
                st = None
        pending = self.dir is not None and (self.dir / "request.json").exists()
        return {"helper_installed": self.helper.is_file(), "status": st, "pending": pending, "last_check": self.last_check}

    def check(self, current: str) -> dict:
        self.last_check = server_update_info(self.gh, current, fresh=True)
        return self.status()

    def _busy(self) -> bool:
        s = self.status()
        st = s["status"] or {}
        running = st.get("state") == "running" and time.time() * 1000 - st.get("time", 0) < self.STALE_MS
        return s["pending"] or running

    def install(self, version: str) -> dict:
        if self.dir is None:
            raise ReleaseError("this server has no data folder")
        if not self.helper.is_file():
            raise ReleaseError("the update helper is not installed: re-run install.sh from a 0.6.24 or newer release once")
        if self._busy():
            raise ReleaseError("an update is already in progress")
        stage = self.dir / "staging"
        if stage.exists():
            for p in stage.iterdir():
                p.unlink()
        meta = stage_server_update(self.gh, version, stage, self.signers)
        req = self.dir / "request.json.tmp"
        req.write_text(json.dumps({"version": version, "requested": int(time.time() * 1000)}))
        os.replace(req, self.dir / "request.json")      # this wakes fet-monitor-update.path
        return meta
