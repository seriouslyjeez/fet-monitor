"""Port 80 for people (user 2026-11-22), and the setup page borrowing it (fetserver/webports.py,
fetsetup.portal.bind_taking_turns). Real sockets on free local ports."""
import http.client
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fetserver import webports
from fetserver.webports import WebPorts, ports_from_env
from fetsetup.portal import bind_taking_turns


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def server(port, word):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(word.encode())

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    return srv


def get(port):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        c.request("GET", "/")
        return c.getresponse().read().decode()
    except OSError:
        return None
    finally:
        c.close()


class Env(unittest.TestCase):
    def test_ports_from_env(self):
        self.assertEqual(ports_from_env(8080, {}), [])
        self.assertEqual(ports_from_env(8080, {"FETM_WEB_PORTS": "80"}), [80])
        self.assertEqual(ports_from_env(8080, {"FETM_WEB_PORTS": " 80, 8080,x,0,70000,80,81"}), [80, 81],
                         "the main port, junk and repeats are left out")


class TakingTurns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.yf = Path(self.tmp.name, "run", "web-port.yield")
        self.port = free_port()
        self.web = WebPorts(lambda p: server(p, "monitor"), [self.port], self.yf)
        self.addCleanup(self.web.close)

    def test_open_close_and_open_again(self):
        self.web.step()
        self.assertEqual(get(self.port), "monitor")
        self.yf.parent.mkdir(parents=True)
        self.yf.write_text("x")
        self.web.step()
        self.assertIsNone(get(self.port), "let go while the setup page has it")
        self.assertEqual(self.web.servers, {})
        self.yf.unlink()
        self.web.step()
        self.assertEqual(get(self.port), "monitor", "taken again")

    def test_a_port_it_cannot_have_is_tried_again(self):
        blocker = server(self.port, "other")
        threading.Thread(target=blocker.serve_forever, daemon=True).start()
        with self.assertLogs("fetserver.webports", "ERROR") as logs:
            self.web.step()
            self.web.step()
        self.assertEqual(len(logs.output), 1, "logged once")
        self.assertEqual(self.web.servers, {})
        blocker.shutdown()
        blocker.server_close()
        self.web.step()
        self.assertEqual(get(self.port), "monitor")

    def test_the_setup_page_takes_port_80_from_a_running_monitor(self):
        stop = threading.Event()
        old = webports.CHECK_S
        webports.CHECK_S = 0.1
        self.addCleanup(setattr, webports, "CHECK_S", old)
        t = threading.Thread(target=self.web.run, args=(stop,), daemon=True)
        t.start()
        self.addCleanup(lambda: (stop.set(), t.join(2)))
        for _ in range(50):
            if get(self.port) == "monitor":
                break
            time.sleep(0.05)
        self.assertEqual(get(self.port), "monitor")
        setup = bind_taking_turns(lambda: server(self.port, "setup"), self.yf)
        threading.Thread(target=setup.serve_forever, daemon=True).start()
        self.assertTrue(self.yf.exists())
        self.assertEqual(get(self.port), "setup")
        setup.shutdown()
        setup.server_close()
        self.yf.unlink()                                  # what serve() does when the setup ends
        for _ in range(50):
            if get(self.port) == "monitor":
                break
            time.sleep(0.05)
        self.assertEqual(get(self.port), "monitor", "FeT Monitor has port 80 again")

    def test_bind_taking_turns_gives_up(self):
        calls = []

        def make():
            calls.append(1)
            raise OSError("in use")
        with self.assertRaises(OSError):
            bind_taking_turns(make, self.yf, tries=3, sleep=lambda s: None)
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.yf.exists(), "serve() removes it in its finally")


if __name__ == "__main__":
    unittest.main()
