# QR codes for FeT Display (a lane's sign-in address), in MicroPython. A port of the parts FeT Display needs of
# "QR Code Generator for JavaScript" by Kazuhiko Arase (MIT; fetserver/web/qrcode.js, which FeT Monitor's pages use;
# NOTICE): byte mode, error correction level M, versions 1 to 10 (up to 213 characters), and the same choice of mask,
# so the display draws the same code as the page. tests/test_display_qr.py compares the two module by module.
# "QR Code" is a registered trademark of DENSO WAVE INCORPORATED.

# version -> (blocks, total codewords, data codewords)[, (blocks, total, data)] at level M
_RS_M = {1: (1, 26, 16), 2: (1, 44, 28), 3: (1, 70, 44), 4: (2, 50, 32), 5: (2, 67, 43), 6: (4, 43, 27), 7: (4, 49, 31),
         8: (2, 60, 38, 2, 61, 39), 9: (3, 58, 36, 2, 59, 37), 10: (4, 69, 43, 1, 70, 44)}
_ALIGN = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34), 7: (6, 22, 38), 8: (6, 24, 42),
          9: (6, 26, 46), 10: (6, 28, 50)}
_ECL_M = 0                                  # level M's bits in the format information
_G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | 1
_G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | 1
_G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1)
_EXP = [0] * 256
_LOG = [0] * 256
for _i in range(8):
    _EXP[_i] = 1 << _i
for _i in range(8, 256):
    _EXP[_i] = _EXP[_i - 4] ^ _EXP[_i - 5] ^ _EXP[_i - 6] ^ _EXP[_i - 8]
for _i in range(255):
    _LOG[_EXP[_i]] = _i
UNSET = 2


def _gexp(n):
    while n < 0:
        n += 255
    while n >= 256:
        n -= 255
    return _EXP[n]


def _poly(num, shift=0):
    off = 0
    while off < len(num) and num[off] == 0:
        off += 1
    return list(num[off:]) + [0] * shift


def _mul(a, b):
    out = [0] * (len(a) + len(b) - 1)
    for i in range(len(a)):
        for j in range(len(b)):
            out[i + j] ^= _gexp(_LOG[a[i]] + _LOG[b[j]])
    return _poly(out)


def _mod(a, e):
    while len(a) - len(e) >= 0:
        ratio = _LOG[a[0]] - _LOG[e[0]]
        a = a[:]
        for i in range(len(e)):
            a[i] ^= _gexp(_LOG[e[i]] + ratio)
        a = _poly(a)
    return a


def _bch_digit(d):
    n = 0
    while d:
        n += 1
        d >>= 1
    return n


def _bch_type_info(data):
    d = data << 10
    while _bch_digit(d) - _bch_digit(_G15) >= 0:
        d ^= _G15 << (_bch_digit(d) - _bch_digit(_G15))
    return ((data << 10) | d) ^ _G15_MASK


def _bch_type_number(data):
    d = data << 12
    while _bch_digit(d) - _bch_digit(_G18) >= 0:
        d ^= _G18 << (_bch_digit(d) - _bch_digit(_G18))
    return (data << 12) | d


def _blocks(version):
    t = _RS_M[version]
    out = []
    for i in range(0, len(t), 3):
        out += [(t[i + 1], t[i + 2])] * t[i]
    return out


class _Bits:
    def __init__(self):
        self.buf = bytearray()
        self.n = 0

    def put(self, num, length):
        for i in range(length):
            self.bit((num >> (length - i - 1)) & 1)

    def bit(self, b):
        if len(self.buf) <= self.n // 8:
            self.buf.append(0)
        if b:
            self.buf[self.n // 8] |= 0x80 >> (self.n % 8)
        self.n += 1


def _data(version, data):
    blocks = _blocks(version)
    total_data = sum(d for _, d in blocks)
    b = _Bits()
    b.put(4, 4)                             # byte mode
    b.put(len(data), 8 if version < 10 else 16)
    for c in data:
        b.put(c, 8)
    if b.n > total_data * 8:
        raise ValueError("too long for a QR code here")
    if b.n + 4 <= total_data * 8:
        b.put(0, 4)
    while b.n % 8:
        b.bit(0)
    while b.n < total_data * 8:
        b.put(0xEC, 8)
        if b.n >= total_data * 8:
            break
        b.put(0x11, 8)
    off, dc, ec = 0, [], []
    for total, d in blocks:
        dc.append(list(b.buf[off:off + d]))
        off += d
        n_ec = total - d
        gen = [1]
        for i in range(n_ec):
            gen = _mul(gen, [1, _gexp(i)])
        m = _mod(_poly(dc[-1], len(gen) - 1), gen)
        ec.append([m[i + len(m) - n_ec] if i + len(m) - n_ec >= 0 else 0 for i in range(n_ec)])
    out = []
    for i in range(max(len(x) for x in dc)):
        for x in dc:
            if i < len(x):
                out.append(x[i])
    for i in range(max(len(x) for x in ec)):
        for x in ec:
            if i < len(x):
                out.append(x[i])
    return out


_MASKS = (lambda i, j: (i + j) % 2 == 0, lambda i, j: i % 2 == 0, lambda i, j: j % 3 == 0, lambda i, j: (i + j) % 3 == 0,
          lambda i, j: (i // 2 + j // 3) % 2 == 0, lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
          lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0, lambda i, j: ((i * j) % 3 + (i + j) % 2) % 2 == 0)


def _make(version, data, test, mask):
    n = version * 4 + 17
    m = [bytearray([UNSET] * n) for _ in range(n)]
    for row, col in ((0, 0), (n - 7, 0), (0, n - 7)):
        for r in range(-1, 8):
            if not 0 <= row + r < n:
                continue
            for c in range(-1, 8):
                if not 0 <= col + c < n:
                    continue
                m[row + r][col + c] = 1 if ((0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) or (2 <= r <= 4 and 2 <= c <= 4)) else 0
    pos = _ALIGN[version]
    for row in pos:
        for col in pos:
            if m[row][col] != UNSET:
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    m[row + r][col + c] = 1 if (r in (-2, 2) or c in (-2, 2) or (r == 0 and c == 0)) else 0
    for i in range(8, n - 8):
        if m[i][6] == UNSET:
            m[i][6] = 1 if i % 2 == 0 else 0
        if m[6][i] == UNSET:
            m[6][i] = 1 if i % 2 == 0 else 0
    bits = _bch_type_info((_ECL_M << 3) | mask)
    for i in range(15):
        v = 0 if test else (bits >> i) & 1
        if i < 6:
            m[i][8] = v
        elif i < 8:
            m[i + 1][8] = v
        else:
            m[n - 15 + i][8] = v
    for i in range(15):
        v = 0 if test else (bits >> i) & 1
        if i < 8:
            m[8][n - i - 1] = v
        elif i < 9:
            m[8][15 - i - 1 + 1] = v
        else:
            m[8][15 - i - 1] = v
    m[n - 8][8] = 0 if test else 1
    if version >= 7:
        bits = _bch_type_number(version)
        for i in range(18):
            v = 0 if test else (bits >> i) & 1
            m[i // 3][i % 3 + n - 8 - 3] = v
            m[i % 3 + n - 8 - 3][i // 3] = v
    inc, row, bit, byte = -1, n - 1, 7, 0
    f = _MASKS[mask]
    col = n - 1
    while col > 0:
        if col == 6:
            col -= 1
        while True:
            for c in range(2):
                if m[row][col - c] == UNSET:
                    dark = byte < len(data) and (data[byte] >> bit) & 1 == 1
                    if f(row, col - c):
                        dark = not dark
                    m[row][col - c] = 1 if dark else 0
                    bit -= 1
                    if bit == -1:
                        byte += 1
                        bit = 7
            row += inc
            if row < 0 or row >= n:
                row -= inc
                inc = -inc
                break
        col -= 2
    return m


def _lost(m):
    n = len(m)
    lost = 0
    for row in range(n):
        for col in range(n):
            same = 0
            dark = m[row][col]
            for r in (-1, 0, 1):
                if not 0 <= row + r < n:
                    continue
                for c in (-1, 0, 1):
                    if not 0 <= col + c < n or (r == 0 and c == 0):
                        continue
                    if dark == m[row + r][col + c]:
                        same += 1
            if same > 5:
                lost += 3 + same - 5
    for row in range(n - 1):
        for col in range(n - 1):
            k = m[row][col] + m[row + 1][col] + m[row][col + 1] + m[row + 1][col + 1]
            if k == 0 or k == 4:
                lost += 3
    for row in range(n):
        r = m[row]
        for col in range(n - 6):
            if r[col] and not r[col + 1] and r[col + 2] and r[col + 3] and r[col + 4] and not r[col + 5] and r[col + 6]:
                lost += 40
    for col in range(n):
        for row in range(n - 6):
            if (m[row][col] and not m[row + 1][col] and m[row + 2][col] and m[row + 3][col] and m[row + 4][col]
                    and not m[row + 5][col] and m[row + 6][col]):
                lost += 40
    dark = sum(sum(r) for r in m)
    return lost + abs(100 * dark / n / n - 50) / 5 * 10


def encode(text):
    """The QR code of an ASCII text (level M): a list of rows, each a bytearray of 1 (dark) and 0 (light)."""
    data = text.encode()
    if any(c > 127 for c in data):
        raise ValueError("ASCII only")
    for version in range(1, 11):
        total = sum(d for _, d in _blocks(version))
        if 4 + 8 + 8 * len(data) <= total * 8 if version < 10 else 4 + 16 + 8 * len(data) <= total * 8:
            break
    else:
        raise ValueError("too long for a QR code here")
    codewords = _data(version, data)
    best, best_mask = None, 0
    for mask in range(8):
        lost = _lost(_make(version, codewords, True, mask))
        if mask == 0 or best > lost:
            best, best_mask = lost, mask
    return _make(version, codewords, False, best_mask)
