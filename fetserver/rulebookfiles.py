"""Local copies of the rulebooks (rulebooks.DOCS), so the book a match is run by is always on the Pi, with or without
internet at the range.

The PDFs are NOT part of FeT Monitor (each is copyrighted by its publisher): the Pi downloads them from the publisher's
own site, or a match director uploads the file. Each copy is checked against the fingerprint (SHA-256) of the edition
FeT Monitor was built against; a different file is kept but marked, because rule numbers may have moved.

Being particular about what we download:
* HTTPS only, and only from the host of that book's own link (also after redirects);
* a size cap, streamed and cut off past it; the file must be a PDF ("%PDF-");
* stored as <data-dir>/rulebooks/<doc id>.pdf with a small .json beside it, written atomically; only those files
  are ever served.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from .rulebooks import DOCS, DOC_BY_ID

MAX_PDF = 32 * 1024 * 1024
log = logging.getLogger("fetserver")


class RulebookError(Exception):
    """A download or upload that cannot be used; the message says why (shown in Match Control)."""


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check):
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_get(url: str, limit: int = MAX_PDF, timeout: float = 60.0) -> bytes:
    """GET url over HTTPS from its own host only (redirects included), at most `limit` bytes."""
    host = urlparse(url).hostname or ""

    def check(u: str) -> None:
        p = urlparse(u)
        if p.scheme != "https" or (p.hostname or "") != host:
            raise RulebookError(f"refusing to download from {p.scheme}://{p.hostname}: not the publisher's site over HTTPS")
    check(url)
    opener = urllib.request.build_opener(_Redirects(check))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (fet-monitor rulebook download)", "Accept": "application/pdf,*/*"})
    try:
        with opener.open(req, timeout=timeout) as r:
            check(r.geturl())
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise RulebookError(f"{declared} bytes is more than the {limit} allowed")
            buf = io.BytesIO()
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                buf.write(chunk)
                if buf.tell() > limit:
                    raise RulebookError(f"more than the {limit} bytes allowed")
            return buf.getvalue()
    except urllib.error.HTTPError as exc:
        raise RulebookError(f"{host} answered {exc.code}: the link may have changed; open the publisher's page and upload the file")
    except urllib.error.URLError as exc:
        raise RulebookError(f"cannot reach {host}: {getattr(exc, 'reason', exc)}")
    except OSError as exc:                         # timeouts, resets
        raise RulebookError(f"cannot reach {host}: {exc}")


class RulebookFiles:
    def __init__(self, data_dir: Optional[Path], fetch: Callable[[str], bytes] = https_get, now: Callable[[], float] = time.time):
        self.dir = Path(data_dir) / "rulebooks" if data_dir is not None else None
        self.fetch, self.now = fetch, now
        self.errors: Dict[str, str] = {}               # the last failed download per document (not stored)
        self.lock = threading.Lock()

    def _meta(self, doc_id: str) -> Optional[dict]:
        if self.dir is None:
            return None
        try:
            meta = json.loads((self.dir / (doc_id + ".json")).read_text())
        except (OSError, ValueError):
            return None
        return meta if (self.dir / (doc_id + ".pdf")).is_file() else None

    def path(self, doc_id: str) -> Optional[Path]:
        """The stored PDF of a known document, or None."""
        if doc_id not in DOC_BY_ID or self._meta(doc_id) is None:
            return None
        return self.dir / (doc_id + ".pdf")

    def add(self, doc_id: str, data: bytes, how: str, by: str = "") -> dict:
        doc = DOC_BY_ID.get(doc_id)
        if doc is None:
            raise RulebookError("unknown rulebook")
        if self.dir is None:
            raise RulebookError("this server has no data folder for rulebooks")
        if not data.startswith(b"%PDF-"):
            raise RulebookError("not a PDF file")
        if len(data) > MAX_PDF:
            raise RulebookError(f"a rulebook is at most {MAX_PDF // (1024 * 1024)} MB")
        sha = hashlib.sha256(data).hexdigest()
        meta = {"sha256": sha, "size": len(data), "current": sha == doc["sha256"], "how": how, "by": by[:60],
                "stored_ms": int(self.now() * 1000)}
        with self.lock:
            self.dir.mkdir(mode=0o750, parents=True, exist_ok=True)
            for suffix, payload in ((".pdf", data), (".json", json.dumps(meta).encode())):
                tmp = self.dir / (doc_id + suffix + ".tmp")
                with open(tmp, "wb") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.dir / (doc_id + suffix))
            self.errors.pop(doc_id, None)
        return meta

    def download(self, doc_id: str, by: str = "") -> dict:
        doc = DOC_BY_ID.get(doc_id)
        if doc is None:
            raise RulebookError("unknown rulebook")
        try:
            data = self.fetch(doc["url"])
            meta = self.add(doc_id, data, "download", by)
        except RulebookError as exc:
            self.errors[doc_id] = str(exc)
            raise
        log.info("rulebook %s downloaded from %s (%s)", doc_id, urlparse(doc["url"]).hostname,
                 "the edition FeT Monitor follows" if meta["current"] else "a DIFFERENT file than the edition FeT Monitor follows")
        return meta

    def download_missing(self) -> List[str]:
        """Fetch every document not stored yet (at start-up, in the background); returns the ids stored."""
        done = []
        for doc in DOCS:
            if self._meta(doc["id"]) is None:
                try:
                    self.download(doc["id"], "automatic")
                    done.append(doc["id"])
                except RulebookError as exc:
                    log.info("rulebook %s not downloaded: %s", doc["id"], exc)
        return done

    def status(self) -> List[dict]:
        """Every document with its local copy: stored (and whether it is the edition FeT Monitor follows) or not."""
        out = []
        for doc in DOCS:
            meta = self._meta(doc["id"])
            out.append(dict(doc, stored=meta, error=self.errors.get(doc["id"]),
                            local=f"/api/v1/rulebooks/{doc['id']}.pdf" if meta else None))
        return out
