# FeT Display talking to FeT Monitor (docs/PROTOCOL.md "Lane displays"): pairing, the view (long poll) and the buttons,
# with asyncio (the screen and the touch keep working while a long poll waits).
# No hardware here: it also runs under MicroPython on a computer (tests/test_display_client.py).
import http

WAIT_S = 25                  # the long poll: FeT Monitor answers at once when something changes, else after this


class NotAssigned(Exception):
    """The token is not (or no longer) a display's: "pending" (waiting for Range Control) or "none" (pair again)."""
    def __init__(self, pairing):
        super().__init__(pairing)
        self.pairing = pairing


class Client:
    def __init__(self, server, hw_id, fw, token=None, secure=False):
        host, _, port = server.partition(":")
        self.host, self.port, self.secure = host, int(port or (443 if secure else 8080)), secure
        self.hw_id, self.fw, self.token = hw_id, fw, token
        self.code = self.code_expires_ms = None

    async def _req(self, method, path, body=None, timeout=10):
        hdr = {"X-FET-Display-FW": self.fw}
        if self.token:
            hdr["Authorization"] = "Bearer " + self.token
        try:
            return await http.arequest(method, self.host, self.port, path, body, hdr, timeout, self.secure)
        except Exception as exc:                       # asyncio's TimeoutError is not an OSError on MicroPython
            if isinstance(exc, OSError):
                raise
            raise OSError("no answer from FeT Monitor (%s)" % type(exc).__name__)

    async def pair(self, kit=False):
        """Ask for a code to show: sets code (and a token kept until Range Control types it). kit: a portable kit
        asking deepTENS (it forwards its target's lines; lines())."""
        req = {"hw_id": self.hw_id, "fw": self.fw}
        if kit:
            req["kit"] = True
        st, body = await self._req("POST", "/api/v1/display/pair", req)
        if st != 200 or not body or "token" not in body:
            raise OSError("pairing refused: %s" % ((body or {}).get("error") or st))
        self.token, self.code = body["token"], body["code"]
        self.code_expires_ms = body.get("expires_in_s", 600) * 1000
        return self.code

    async def view(self, rev=None, wait=0):
        """The target's view (a dict), at once or when it differs from rev (up to wait seconds). NotAssigned, or
        OSError when FeT Monitor cannot be reached."""
        path = "/api/v1/display/view"
        if rev:
            path += "?rev=%s&wait=%d" % (rev, wait)
        st, body = await self._req("GET", path, timeout=wait + 10)
        if st == 401:
            raise NotAssigned((body or {}).get("pairing", "none"))
        if st != 200 or not isinstance(body, dict):
            raise OSError("FeT Monitor answered %d" % st)
        return body

    async def act(self, action, **fields):
        """A button: the new view, or ValueError with what FeT Monitor said (to show)."""
        st, body = await self._req("POST", "/api/v1/display/action", dict(fields, action=action))
        if st == 401:
            raise NotAssigned((body or {}).get("pairing", "none"))
        if st != 200:
            raise ValueError((body or {}).get("error") or "not done (%d)" % st)
        return body

    async def lines(self, lines):
        """A portable kit's lines to deepTENS: its answer ({"acked": [[device_id, boot, seq]], "rejected": [...]}).
        NotAssigned while the code waits ("pending") or when the kit was removed ("none"); ValueError when deepTENS
        refuses the request as a whole; OSError when it cannot be reached."""
        st, body = await self._req("POST", "/api/v1/display/lines", {"lines": lines}, timeout=30)
        if st == 401:
            raise NotAssigned((body or {}).get("pairing", "none"))
        if st != 200 or not isinstance(body, dict):
            raise ValueError((body or {}).get("error") or "deepTENS answered %d" % st)
        return body
