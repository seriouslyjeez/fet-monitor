"""Extra web ports for people (user 2026-11-22: "port 8080 ... make it difficult for users to interact with the local
range ... should run on port 80, even if it presents a small risk"). FeT Monitor also answers on port 80, so a phone
opens http://<Pi address>/ with no port to type. Port 8080 (--port) stays: every target is set up to report there, and
the flash station keeps giving targets that port (FetHTTPServer.device_port).

The first-time setup page (fetsetup, root) also needs port 80, on the Pi's own setup network. While it runs it leaves
YIELD_FILE; FeT Monitor then closes its extra ports (targets keep reporting on 8080), and opens them again once the
file is gone. The service passes the ports in FETM_WEB_PORTS (not a command-line option): an update that rolls back
keeps the new service file, and an older version ignores the variable."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Dict, List

log = logging.getLogger("fetserver.webports")

YIELD_FILE = Path("/run/fet-setup/web-port.yield")
ENV = "FETM_WEB_PORTS"
CHECK_S = 2.0


def ports_from_env(primary: int, environ=os.environ) -> List[int]:
    """FETM_WEB_PORTS="80" (comma separated); anything not a port, or the primary port, is left out."""
    out = []
    for part in (environ.get(ENV) or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 < int(part) < 65536 and int(part) != primary and int(part) not in out:
            out.append(int(part))
    return out


class WebPorts:
    def __init__(self, make: Callable[[int], object], ports: List[int], yield_file: Path = YIELD_FILE):
        self.make, self.ports, self.yield_file = make, list(ports), Path(yield_file)
        self.servers: Dict[int, object] = {}
        self._failed: Dict[int, str] = {}

    def step(self) -> None:
        """Open the ports that should be open, close them all while the setup page has port 80."""
        if self.yield_file.exists():
            for port, srv in list(self.servers.items()):
                srv.shutdown()
                srv.server_close()
                del self.servers[port]
                log.warning("port %d left to the setup page", port)
            return
        for port in self.ports:
            if port in self.servers:
                continue
            try:
                srv = self.make(port)
            except OSError as exc:                     # in use or not allowed: tried again later, logged once
                if self._failed.get(port) != str(exc):
                    log.error("cannot listen on port %d (%s): still on the main port", port, exc)
                    self._failed[port] = str(exc)
                continue
            self._failed.pop(port, None)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.servers[port] = srv
            log.info("also listening on port %d", port)

    def run(self, stop: threading.Event) -> None:
        while True:
            try:
                self.step()
            except Exception:
                log.exception("web ports")
            if stop.wait(CHECK_S):
                break
        self.close()

    def close(self) -> None:
        for srv in self.servers.values():
            srv.shutdown()
            srv.server_close()
        self.servers.clear()
