"""Local copies of the rulebooks: downloaded from the publishers (never from anywhere else), or uploaded by a match
director; checked against the edition FeT Monitor follows; served to anyone on the club network."""
import hashlib
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fetserver import rulebookfiles as rf
from fetserver.rulebookfiles import RulebookError, RulebookFiles, https_get
from fetserver.rulebooks import BOOK_DOC, BOOKS, DOCS, DOC_BY_ID, NONE, texts
from fetserver.server import make_server
from tests.test_match_http import MatchHttpBase

PDF = b"%PDF-1.7\n a rulebook \n%%EOF"


def known(data):
    """DOC_BY_ID with every document's fingerprint set to this file's, so it counts as the edition we follow."""
    sha = hashlib.sha256(data).hexdigest()
    return {k: dict(v, sha256=sha) for k, v in DOC_BY_ID.items()}


class Manifest(unittest.TestCase):
    def test_every_book_has_its_document_from_its_publisher(self):
        self.assertEqual({d["id"] for d in DOCS}, {"issf-2026", "usas-2025", "cmp-ag-2026", "cmp-3par-2026", "nra-par-2025"})
        for book, doc in BOOK_DOC.items():
            self.assertEqual(DOC_BY_ID[doc]["book"], book)
            self.assertEqual(texts(book)["doc"], doc)
        self.assertIsNone(texts(NONE)["doc"])
        self.assertNotIn(NONE, BOOK_DOC)
        hosts = {"issf-2026": "backoffice.issf-sports.org", "usas-2025": "ncaaorg.s3.amazonaws.com", "cmp-ag-2026": "thecmp.org",
                 "cmp-3par-2026": "thecmp.org", "nra-par-2025": "competitions.nra.org"}
        for d in DOCS:
            self.assertTrue(d["url"].startswith("https://" + hosts[d["id"]] + "/"), d["id"])
            self.assertTrue(d["page"].startswith("https://") and len(d["sha256"]) == 64 and d["size"] > 100_000, d["id"])
        self.assertEqual(DOC_BY_ID["usas-2025"]["publisher"], "USA Shooting")
        self.assertEqual(BOOKS["CMP-AG"]["url"], "https://thecmp.org/competitions/cmp-competitions-rulebooks/")


class Store(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.fetched = []

    def store(self, data=PDF, fail=None):
        def fetch(url):
            self.fetched.append(url)
            if fail:
                raise RulebookError(fail)
            return data
        return RulebookFiles(self.dir, fetch=fetch, now=lambda: 1000.0)

    def test_download_checks_the_edition(self):
        s = self.store()
        with mock.patch.object(rf, "DOC_BY_ID", known(PDF)):
            meta = s.download("cmp-ag-2026", "md")
        self.assertEqual((meta["current"], meta["how"], meta["by"], meta["size"], meta["stored_ms"]), (True, "download", "md", len(PDF), 1_000_000))
        self.assertEqual(self.fetched, [DOC_BY_ID["cmp-ag-2026"]["url"]])
        self.assertEqual(s.path("cmp-ag-2026").read_bytes(), PDF)
        self.assertFalse(s.download("cmp-3par-2026")["current"], "a different file: kept, marked")
        st = {x["id"]: x for x in s.status()}
        self.assertEqual(st["cmp-ag-2026"]["local"], "/api/v1/rulebooks/cmp-ag-2026.pdf")
        self.assertFalse(st["cmp-3par-2026"]["stored"]["current"])
        self.assertIsNone(st["issf-2026"]["stored"])
        self.assertIsNone(st["issf-2026"]["local"])
        self.assertEqual(sorted(p.name for p in (self.dir / "rulebooks").iterdir()),
                         ["cmp-3par-2026.json", "cmp-3par-2026.pdf", "cmp-ag-2026.json", "cmp-ag-2026.pdf"], "no temporary files left")

    def test_what_is_refused(self):
        s = self.store(data=b"<html>moved</html>")
        with self.assertRaisesRegex(RulebookError, "not a PDF"):
            s.download("issf-2026")
        self.assertEqual(s.status()[0]["error"], "not a PDF file", "the reason is shown")
        self.assertIsNone(s.path("issf-2026"))
        for bad in ("../t", "nope", "issf-2026/../../t.db"):
            with self.assertRaises(RulebookError):
                s.add(bad, PDF, "upload")
            self.assertIsNone(s.path(bad))
        with self.assertRaisesRegex(RulebookError, "no data folder"):
            RulebookFiles(None).add("issf-2026", PDF, "upload")
        with mock.patch.object(rf, "MAX_PDF", len(PDF) - 1):
            with self.assertRaisesRegex(RulebookError, "at most"):
                s.add("issf-2026", PDF, "upload")
        with mock.patch.object(rf, "MAX_PDF", len(PDF)):
            s.add("issf-2026", PDF, "upload")                  # exactly the limit is fine
        for suffix in (".pdf", ".json"):                        # files that are not one of our documents are never served
            (self.dir / "rulebooks" / ("x-unknown" + suffix)).write_text("{}" if suffix == ".json" else "%PDF-")
        self.assertIsNone(s.path("x-unknown"))
        s.add("issf-2026", PDF, "upload", "md")
        self.assertIsNone(s.status()[0]["error"], "a stored copy clears the error")
        (self.dir / "rulebooks" / "issf-2026.pdf").unlink()
        self.assertIsNone(s.path("issf-2026"), "metadata without its file is not a stored copy")

    def test_missing_ones_at_start_up(self):
        s = self.store()
        s.add("issf-2026", PDF, "upload")
        self.assertEqual(s.download_missing(), ["usas-2025", "cmp-ag-2026", "cmp-3par-2026", "nra-par-2025"])
        self.assertNotIn(DOC_BY_ID["issf-2026"]["url"], self.fetched, "a stored copy is not fetched again")
        self.assertEqual(s.download_missing(), [])
        off = self.store(fail="cannot reach thecmp.org")
        off.dir = self.dir / "other"
        self.assertEqual(off.download_missing(), [], "no internet: nothing stored, no crash")
        self.assertEqual({x["error"] for x in off.status()}, {"cannot reach thecmp.org"})

    def test_only_https_from_the_publishers_host(self):
        for url in ("http://thecmp.org/x.pdf", "ftp://thecmp.org/x.pdf", "file:///etc/passwd"):
            with self.assertRaisesRegex(RulebookError, "refusing"):
                https_get(url)

    def test_redirects_stay_on_the_publishers_host(self):
        seen = []

        class Opener:
            def __init__(self, handler):
                self.handler = handler

            def open(self, req, timeout):
                seen.append(req.full_url)
                self.handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/x.pdf")
        with mock.patch.object(rf.urllib.request, "build_opener", lambda h: Opener(h)):
            with self.assertRaisesRegex(RulebookError, "refusing to download from https://evil.example"):
                https_get("https://thecmp.org/x.pdf")
        self.assertEqual(seen, ["https://thecmp.org/x.pdf"])


class OverHttp(MatchHttpBase):
    def setUp(self):
        super().setUp()
        self.files = RulebookFiles(self.dir, fetch=lambda url: PDF)
        self.server.rulebooks = self.files

    def send(self, path, body, ctype, who=None, length=None):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        h = {"Content-Type": ctype, "X-FET-Admin": "1", "Content-Length": str(len(body) if length is None else length)}
        if who:
            h["Cookie"] = self.cookies[who]
        c.request("POST", path, body=body, headers=h)
        r = c.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        c.close()
        return out

    def test_public_list_and_files(self):
        st, body, _ = self.req("GET", "/api/v1/rulebooks", header=False)
        self.assertEqual((st, len(body["docs"]), len(body["repositories"])), (200, 5, 6))
        self.assertIn({"name": "CMP competition rulebooks", "url": "https://thecmp.org/competitions/cmp-competitions-rulebooks/"}, body["repositories"])
        self.assertEqual(self.raw("/api/v1/rulebooks/cmp-ag-2026.pdf")[0], 404)
        self.files.add("cmp-ag-2026", PDF, "upload")
        st, ctype, disp, data = self.raw("/api/v1/rulebooks/cmp-ag-2026.pdf")
        self.assertEqual((st, ctype, disp, data), (200, "application/pdf", 'inline; filename="cmp-ag-2026.pdf"', PDF))
        for bad in ("/api/v1/rulebooks/..%2Ft.db.pdf", "/api/v1/rulebooks/cmp-ag-2026.json", "/api/v1/rulebooks/nope.pdf"):
            self.assertEqual(self.raw(bad)[0], 404, bad)

    def test_director_downloads_and_uploads(self):
        self.assertEqual(self.req("POST", "/api/v1/match/rulebooks/issf-2026/download", {})[0], 401)
        self.login("ro", "officer-pass")
        self.assertEqual(self.req("POST", "/api/v1/match/rulebooks/issf-2026/download", {}, "ro")[0], 403)
        self.assertEqual(self.send("/api/v1/match/rulebooks/issf-2026/upload", PDF, "application/pdf", "ro")[0], 403)
        self.assertEqual(self.req("POST", "/api/v1/match/rulebooks/issf-2026/download", {}, header=False)[0], 403, "cross-site guard")
        self.login("md", "director-pass")
        st, body, _ = self.req("POST", "/api/v1/match/rulebooks/issf-2026/download", {}, "md")
        self.assertEqual((st, body["stored"]["how"], body["stored"]["by"]), (200, "download", "md"))
        self.assertEqual(self.req("POST", "/api/v1/match/rulebooks/nope/download", {}, "md")[0], 404)
        self.assertEqual(self.send("/api/v1/match/rulebooks/usas-2025/upload", PDF, "application/octet-stream", "md")[0], 415)
        self.assertEqual(self.send("/api/v1/match/rulebooks/usas-2025/upload", b"x", "application/pdf", "md", length=rf.MAX_PDF + 1)[0], 413)
        st, body = self.send("/api/v1/match/rulebooks/usas-2025/upload", b"not a pdf", "application/pdf", "md")
        self.assertEqual((st, body["error"]), (400, "not a PDF file"))
        st, body = self.send("/api/v1/match/rulebooks/usas-2025/upload", PDF, "application/pdf", "md")
        self.assertEqual((st, body["stored"]["how"], body["stored"]["current"]), (200, "upload", False))
        self.assertEqual(self.raw("/api/v1/rulebooks/usas-2025.pdf")[3], PDF)

    def test_no_data_folder(self):
        server = make_server(self.core, "t", "127.0.0.1", 0)
        self.addCleanup(server.server_close)
        self.assertIsNone(server.rulebooks.path("issf-2026"))


if __name__ == "__main__":
    unittest.main()
