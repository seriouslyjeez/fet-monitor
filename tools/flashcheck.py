#!/usr/bin/env python3
"""Checks that a flash chip really has the capacity it reports (fake 4 MB parts usually have 1 MB or less
and silently wrap around). Every 4 KB sector gets unique content, the file is written to the chip and read
back, and the two are compared sector by sector.

    python3 flashcheck.py make  pattern.bin [size_bytes]      # default 4 MiB
    python3 flashcheck.py compare pattern.bin readback.bin

Then, with esptool v5:
    esptool --port PORT --baud 460800 write-flash 0 pattern.bin
    esptool --port PORT --baud 460800 read-flash 0 0x400000 readback.bin
This ERASES the chip, so only use it on a module whose contents you do not need.
"""
import hashlib
import sys

SECTOR = 4096


def sector(i: int) -> bytes:
    out = b""
    n = 0
    while len(out) < SECTOR:                               # unique, incompressible, never all 0x00/0xFF
        out += hashlib.sha256(b"fet-flashcheck-%d-%d" % (i, n)).digest()
        n += 1
    return out[:SECTOR]


def make(path: str, size: int) -> None:
    with open(path, "wb") as f:
        for i in range(size // SECTOR):
            f.write(sector(i))
    print(f"wrote {path}: {size} bytes, {size // SECTOR} unique sectors")


def compare(pattern: str, back: str) -> int:
    a, b = open(pattern, "rb").read(), open(back, "rb").read()
    n = len(a) // SECTOR
    if len(b) < len(a):
        print(f"FAIL: read back only {len(b)} bytes, expected {len(a)}: the read was cut short, try again")
        return 1
    bad = [i for i in range(n) if a[i * SECTOR:(i + 1) * SECTOR] != b[i * SECTOR:(i + 1) * SECTOR]]
    if not bad:
        print(f"PASS: all {n} sectors ({len(a) // 1024} KB) read back exactly as written. The capacity is real.")
        return 0
    first = bad[0]
    print(f"FAIL: {len(bad)} of {n} sectors differ. First bad sector {first} (offset 0x{first * SECTOR:X}).")
    # A smaller chip wraps around: writing sector j lands on sector j mod P, so each sector ends up holding the LAST
    # data written to its slot. Find the power-of-two size P (in sectors) that explains the read-back exactly.
    p = 1
    while p < n:
        if all(b[i * SECTOR:(i + 1) * SECTOR] == a[(((n - 1 - i % p) // p) * p + i % p) * SECTOR:][:SECTOR] for i in range(n)):
            print(f"  The read-back is exactly what a {p * SECTOR // 1024} KB chip that wraps around would give.")
            print(f"  Real capacity: {p * SECTOR // 1024} KB, not {n * SECTOR // 1024} KB. Do not trust this module's flash size.")
            return 1
        p *= 2
    ok = sum(1 for i in range(n) if i not in set(bad))
    print("  The pattern is damaged, not simply wrapped: bad flash, a bad connection, or an unstable programmer. "
          "Try a lower --baud (115200) and run it again.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "make":
        make(sys.argv[2], int(sys.argv[3], 0) if len(sys.argv) > 3 else 4 * 1024 * 1024)
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)
        sys.exit(2)
