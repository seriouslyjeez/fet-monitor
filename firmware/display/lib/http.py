# A small HTTP/1.1 client for FeT Display (JSON to and from FeT Monitor): one request per connection, a timeout on
# every step, the body read to its Content-Length. Runs under MicroPython on the CYD and on a computer.
import json
import socket


class HttpError(OSError):
    pass


def request(method, host, port, path, body=None, headers=None, timeout=10):
    """(status, parsed JSON or None). OSError (HttpError) when FeT Monitor cannot be reached or answers nonsense."""
    data = json.dumps(body).encode() if body is not None else b""
    head = "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\nAccept: application/json\r\n" % (method, path, host, port)
    if body is not None:
        head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(data)
    for k, v in (headers or {}).items():
        head += "%s: %s\r\n" % (k, v)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(socket.getaddrinfo(host, port)[0][-1])
        s.send((head + "\r\n").encode() + data)
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = s.recv(1024)
            if not c:
                raise HttpError("closed before the answer")
            buf += c
            if len(buf) > 8192:
                raise HttpError("headers too long")
        h, _, rest = buf.partition(b"\r\n\r\n")
        lines = h.decode().split("\r\n")
        try:
            status = int(lines[0].split(" ")[1])
        except (IndexError, ValueError):
            raise HttpError("not HTTP: " + lines[0][:40])
        length = None
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            if k.strip().lower() == "content-length":
                length = int(v.strip())
        while length is None or len(rest) < length:
            c = s.recv(2048)
            if not c:
                break
            rest += c
            if len(rest) > 65536:
                raise HttpError("answer too long")
        if length is not None and len(rest) < length:
            raise HttpError("answer cut short")
        try:
            return status, json.loads(rest) if rest else None
        except ValueError:
            return status, None
    finally:
        s.close()


async def arequest(method, host, port, path, body=None, headers=None, timeout=10):
    """request() for asyncio: the screen and the touch keep working while FeT Monitor holds a long poll."""
    import asyncio
    return await asyncio.wait_for(_arequest(method, host, port, path, body, headers), timeout)


async def _arequest(method, host, port, path, body, headers):
    import asyncio
    data = json.dumps(body).encode() if body is not None else b""
    head = "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\nAccept: application/json\r\n" % (method, path, host, port)
    if body is not None:
        head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(data)
    for k, v in (headers or {}).items():
        head += "%s: %s\r\n" % (k, v)
    r, w = await asyncio.open_connection(host, port)
    try:
        w.write((head + "\r\n").encode() + data)
        await w.drain()
        line = await r.readline()
        try:
            status = int(line.split(b" ")[1])
        except (IndexError, ValueError):
            raise HttpError("not HTTP: %r" % line[:40])
        length = None
        while True:
            ln = await r.readline()
            if not ln:
                raise HttpError("closed before the answer")
            if ln in (b"\r\n", b"\n"):
                break
            k, _, v = ln.decode().partition(":")
            if k.strip().lower() == "content-length":
                length = int(v.strip())
        if length is not None:
            if length > 65536:
                raise HttpError("answer too long")
            rest = await r.readexactly(length) if length else b""
        else:
            rest = await r.read(65536)
        try:
            return status, json.loads(rest) if rest else None
        except ValueError:
            return status, None
    finally:
        w.close()
        await w.wait_closed()
