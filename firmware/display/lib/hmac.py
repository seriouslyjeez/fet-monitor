# MicroPython stand-in (FeT Display): HMAC (RFC 2104) as fetcore uses it: hmac.new(key, msg, hashlib.sha256)
# .digest() and hmac.compare_digest. Checked against CPython's hmac by tests/test_micropython.py.


class _HMAC:
    def __init__(self, key, msg, digestmod):
        block = 64                                      # SHA-256
        if len(key) > block:
            key = digestmod(key).digest()
        key = key + b"\x00" * (block - len(key))
        self._outer = digestmod(bytes(b ^ 0x5C for b in key))
        self._inner = digestmod(bytes(b ^ 0x36 for b in key))
        if msg:
            self._inner.update(msg)

    def update(self, msg):
        self._inner.update(msg)

    def digest(self):
        self._outer.update(self._inner.digest())
        return self._outer.digest()


def new(key, msg=None, digestmod=None):
    if digestmod is None:
        raise TypeError("digestmod is required")
    return _HMAC(key, msg, digestmod)


def compare_digest(a, b):
    """Equal, in a time that does not depend on where they differ."""
    if isinstance(a, str):
        a = a.encode()
    if isinstance(b, str):
        b = b.encode()
    diff = len(a) ^ len(b)
    for x, y in zip(a, b if len(a) == len(b) else a):
        diff |= x ^ y
    return diff == 0
