"""docs/PROTOCOL.md and tools/protocol_check.py: FeT Monitor passes the check, and the check catches a server that
gets the protocol wrong (a repeat not acknowledged: the bridge would resend it forever)."""
import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server

ROOT = Path(__file__).resolve().parent.parent


def checker():
    spec = importlib.util.spec_from_file_location("protocol_check", ROOT / "tools/protocol_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_check(url, token):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = checker().main([url, token])
    return rc, out.getvalue()


class NaiveServer(BaseHTTPRequestHandler):
    """Acknowledges each identity only the first time it sees it (wrong)."""
    seen = set()

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("X-FET-Token") != "tok":
            return self.answer(401, {"error": "bad token"})
        if self.path.endswith("/heartbeat"):
            return self.answer(200, {"server_time_ms": 1, "heartbeat_interval_ms": 5000, "commands": []})
        if len(body.get("events", [])) > 50 or not str(body.get("device_id", "")).replace("-", "").isalnum():
            return self.answer(400, {"error": "bad"})
        acked, rejected = [], []
        for e in body["events"]:
            ident = (body["boot_count"], e["seq"])
            if e.get("type") != "shot":
                rejected.append({"seq": e["seq"], "reason": "type"})
            elif ident not in NaiveServer.seen:
                NaiveServer.seen.add(ident)
                acked.append(list(ident))
        self.answer(200, {"acked": acked, "rejected": rejected, "server_time_ms": 1})

    def answer(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class Protocol(unittest.TestCase):
    def test_fet_monitor_passes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = Core(Database(str(Path(tmp.name) / "t.db")))
        srv = make_server(core, "the-token", "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        rc, out = run_check(f"http://127.0.0.1:{srv.server_address[1]}", "the-token")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("FAIL", out)
        n = core.db.conn().execute("SELECT COUNT(*) FROM shots WHERE device_id='protocol-check'").fetchone()[0]
        self.assertEqual(n, 3, "three shots stored once each, though one batch was sent twice")

    def test_a_server_that_does_not_ack_repeats_fails(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), NaiveServer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        rc, out = run_check(f"http://127.0.0.1:{srv.server_address[1]}", "tok")
        self.assertEqual(rc, 1)
        self.assertIn("FAIL  a repeat is acknowledged again", out)

    def test_the_document_names_what_the_code_does(self):
        doc = (ROOT / "docs/PROTOCOL.md").read_text()
        from fetserver import core
        self.assertIn(f"At most **{core.MAX_EVENTS_PER_BATCH} events**", doc)
        for t in sorted(core.EVENT_TYPES):
            self.assertIn(f"`{t}`", doc)
        for t in sorted(core.COMMAND_TYPES | {"fw_update"}):
            self.assertIn(f"| `{t}` |", doc)
        self.assertIn(f"`reanchored`", doc)
        self.assertIn("X-FET-Token", doc)


if __name__ == "__main__":
    unittest.main()
