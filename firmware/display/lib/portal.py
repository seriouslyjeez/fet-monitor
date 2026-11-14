# FeT Display's setup portal: an open WiFi network with a DNS that answers every name with the display's address (so a
# phone opens the setup page by itself) and a small web server with the setup page. Network-independent parts (DNS
# answers, HTTP parsing, the page) also run under MicroPython on a computer, where tests drive them on other ports.
import select
import socket

import config

AP_IP = "192.168.4.1"
STYLE = ("<meta name=viewport content='width=device-width,initial-scale=1'><style>"
         "body{font:16px system-ui,sans-serif;max-width:30rem;margin:1rem auto;padding:0 1rem;color:#222}"
         "h1{font-size:1.3rem}label{display:block;margin:.8rem 0 .2rem;font-weight:600}"
         "input,select,button{font:inherit;padding:.5rem;width:100%;box-sizing:border-box}"
         "button{margin-top:1rem;background:#0b5;color:#fff;border:0;border-radius:6px}"
         ".err{background:#fde;border:1px solid #c33;padding:.6rem;border-radius:6px}"
         ".note{color:#666;font-size:.85rem}</style>")
CAPTIVE = ("/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html", "/ncsi.txt",
           "/connecttest.txt", "/redirect", "/success.txt", "/canonical.html")


def html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def unquote(s):
    """application/x-www-form-urlencoded value -> str (UTF-8)."""
    b = s.replace("+", " ").encode()
    out = bytearray()
    i = 0
    while i < len(b):
        if b[i] == 0x25 and i + 2 < len(b):              # %XX
            try:
                out.append(int(chr(b[i + 1]) + chr(b[i + 2]), 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(b[i])
        i += 1
    return _text(bytes(out))


def _text(b):
    try:
        return b.decode("utf-8")
    except UnicodeError:
        return "".join(chr(c) if c < 128 else "?" for c in b)


def form(body):
    out = {}
    for pair in body.split("&"):
        if pair:
            k, _, v = pair.partition("=")
            out[unquote(k)] = unquote(v)
    return out


def dns_answer(query, ip=AP_IP):
    """The answer to one DNS query: the display's address for any name (type A); no address for other types."""
    if len(query) < 17 or query[2] & 0x80 or query[4:6] != b"\x00\x01":
        return None                                      # not a plain query with one question
    end = 12
    while end < len(query) and query[end]:
        end += query[end] + 1
    end += 5                                             # the zero byte, type, class
    if end > len(query):
        return None
    qtype = query[end - 4:end - 2]
    head = query[:2] + b"\x81\x80" + b"\x00\x01" + (b"\x00\x01" if qtype == b"\x00\x01" else b"\x00\x00") + b"\x00\x00\x00\x00"
    ans = b""
    if qtype == b"\x00\x01":
        ans = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + bytes(int(p) for p in ip.split("."))
    return head + query[12:end] + ans


def parse_request(data):
    """(method, path, headers, body) from a whole HTTP request, or None if it is not one (yet)."""
    head, sep, body = data.partition(b"\r\n\r\n")
    if not sep:
        return None
    lines = _text(head).split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return None
    hdr = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdr[k.strip().lower()] = v.strip()
    return parts[0], parts[1].split("?")[0], hdr, body


class Portal:
    """The setup page. name: the network's name; current: the settings now (or None); scan(): network names, best
    first; saved(settings): called after the answer page is sent (it saves and restarts)."""

    def __init__(self, name, current=None, scan=lambda: [], saved=lambda c: None, ip=AP_IP):
        self.name, self.current, self.scan, self.saved, self.ip = name, current or {}, scan, saved, ip

    def page(self, title, body):
        return ("<!doctype html><html><head><title>FeT Display setup</title>" + STYLE + "</head><body><h1>" + html(title) +
                "</h1>" + body + "</body></html>")

    def setup_page(self, err=None, values=None):
        v = values or self.current
        nets = self.scan()
        server = v.get("server", "")
        return self.page("FeT Display setup", (
            ("<p class=err>" + html(err) + "</p>" if err else "") +
            "<p>This display shows one target's shots at a firing point. It joins the range WiFi and asks FeT Monitor for "
            "a code; Range Control then chooses its target.</p>"
            "<form method=post action=/save>"
            "<label>WiFi network</label><input name=ssid list=nets maxlength=32 required autocapitalize=off value=\"" +
            html(v.get("ssid", "")) + "\"><datalist id=nets>" + "".join("<option value=\"" + html(n) + "\">" for n in nets) + "</datalist>"
            "<p class=note>" + ("Networks heard: " + html(", ".join(nets[:8])) if nets else "No networks heard: type its name.") +
            " 2.4 GHz only.</p>"
            "<label>WiFi password</label><input name=password type=password maxlength=63 placeholder=\"" +
            ("unchanged" if v.get("password") else "") + "\">"
            "<label style='font-weight:400'><input type=checkbox name=open value=1 style='width:auto'> This network has no password</label>"
            "<label>FeT Monitor's address (the Raspberry Pi)</label><input name=server maxlength=21 required autocapitalize=off "
            "placeholder='192.168.1.20:8080' value=\"" + html(server) + "\">"
            "<p class=note>Its IP address and port, as Range Control shows it: e.g. 192.168.1.20:8080.</p>"
            "<button>Save and restart</button></form>"
            "<p class=note>To come back here later: switch the display off and on 3 times quickly, or tap Setup on its "
            "screen when it cannot reach the WiFi or FeT Monitor.</p>"))

    def handle(self, req):
        """(status, headers, body bytes, settings or None) for one request."""
        method, path, hdr, body = req
        host = hdr.get("host", "")
        if path in CAPTIVE or (host and host.split(":")[0] not in (self.ip, "localhost", "127.0.0.1")):
            return 302, {"Location": "http://%s/" % self.ip}, b"", None
        if method == "POST" and path == "/save":
            f = form(_text(body))
            if not f.get("password") and self.current.get("password") and f.get("open") != "1" \
                    and f.get("ssid", "").strip() == self.current.get("ssid"):
                f["password"] = self.current["password"]            # left empty: unchanged
            try:
                c = config.check(f)
            except ValueError as exc:
                return 200, {}, self.setup_page(str(exc), dict(f, password="")).encode(), None
            for k in ("token", "invert"):
                if k in self.current and c["server"] == self.current.get("server"):
                    c[k] = self.current[k]                           # still the same FeT Monitor: keep the pairing
            return 200, {}, self.page("Saved", "<p>The display restarts and joins <b>" + html(c["ssid"]) +
                                      "</b>. Then it shows a code: type it in FeT Monitor, Range Control &rarr; Lane displays.</p>").encode(), c
        if method == "GET" and path == "/":
            return 200, {}, self.setup_page().encode(), None
        return 302, {"Location": "http://%s/" % self.ip}, b"", None

    def serve(self, http_port=80, dns_port=53, tick=lambda: None, bind="0.0.0.0", stop=lambda: False):
        """Answer DNS and HTTP until a setup is saved (then saved(settings) is called) or stop() is true."""
        dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dns.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        dns.bind(socket.getaddrinfo(bind, dns_port)[0][-1])
        web = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        web.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        web.bind(socket.getaddrinfo(bind, http_port)[0][-1])
        web.listen(3)
        p = select.poll()
        p.register(dns, select.POLLIN)
        p.register(web, select.POLLIN)
        try:
            while not stop():
                tick()
                for obj, ev in p.poll(100):
                    if obj is dns or obj == dns:
                        try:
                            q, addr = dns.recvfrom(512)
                            a = dns_answer(q, self.ip)
                            if a:
                                dns.sendto(a, addr)
                        except OSError:
                            pass
                    elif obj is web or obj == web:
                        done = self._one(web)
                        if done is not None:
                            self.saved(done)
                            return done
        finally:
            dns.close()
            web.close()

    def _one(self, web):
        conn, _ = web.accept()
        conn.settimeout(3)
        try:
            data = b""
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
                req = parse_request(data)
                if req is not None:
                    n = int(req[2].get("content-length", "0") or 0)
                    if len(req[3]) >= n or len(data) > 4096:
                        break
                if len(data) > 4096:
                    break
            req = parse_request(data)
            if req is None:
                return None
            status, hdr, body, settings = self.handle(req)
            head = "HTTP/1.1 %d %s\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\nConnection: close\r\nCache-Control: no-store\r\n" % (
                status, "OK" if status == 200 else "Found", len(body))
            for k, v in hdr.items():
                head += "%s: %s\r\n" % (k, v)
            conn.send((head + "\r\n").encode() + body)
            return settings
        except OSError:
            return None
        finally:
            conn.close()
