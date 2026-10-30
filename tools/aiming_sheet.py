#!/usr/bin/env python3
"""Printable aiming sheets for measuring a target's accuracy (ground truth for the target firmware work).

    python3 tools/aiming_sheet.py [out.pdf]        # default dist/aiming-sheet.pdf

A 5 x 5 grid of aiming dots at 7 mm pitch around the centre of the rifle faceplate opening (50 mm across, drawing
FET-016), split like a checkerboard over two sheets (A: 13 dots, B: 12) so that holes on one sheet are about 10 mm
apart and cannot merge. Each sheet is taped over the faceplate, shot one dot at a time in the numbered order, then
scanned; the holes are measured against the printed centre lines. US Letter, actual size, plain PDF (stdlib only).
"""
from __future__ import annotations

import sys
from pathlib import Path

MM = 72 / 25.4                       # points per mm
PAGE_W, PAGE_H = 215.9, 279.4        # US Letter in mm
PITCH = 7.0                          # mm between grid dots
APERTURE = 50.0                      # faceplate opening (FET-016), mm
CX, CY = PAGE_W / 2, 150.0           # where the centre of the opening goes on the page
DOT = 1.6                            # aiming dot diameter, mm
KEY_SCALE = 1.6                      # the numbered key drawn beside the target, enlarged


def grid():
    """(row, col) -> (x, y) in mm from the centre; row 0 at the top, col 0 on the left."""
    return {(r, c): ((c - 2) * PITCH, (2 - r) * PITCH) for r in range(5) for c in range(5)}


def sheet_dots(sheet: str):
    """The dots of sheet A ((row + col) even) or B (odd), in shooting order (row by row, left to right)."""
    want = 0 if sheet == "A" else 1
    return [(rc, xy) for rc, xy in sorted(grid().items()) if (rc[0] + rc[1]) % 2 == want]


class Page:
    def __init__(self):
        self.ops = [f"{MM:.6f} 0 0 {MM:.6f} 0 0 cm", "1 J 1 j"]         # draw in millimetres

    def width(self, w):
        self.ops.append(f"{w:.3f} w")

    def gray(self, g):
        self.ops.append(f"{g:.2f} G {g:.2f} g")

    def dash(self, on=0.0, off=0.0):
        self.ops.append(f"[{on:.2f} {off:.2f}] 0 d" if on else "[] 0 d")

    def line(self, x1, y1, x2, y2):
        self.ops.append(f"{x1:.3f} {y1:.3f} m {x2:.3f} {y2:.3f} l S")

    def rect(self, x, y, w, h):
        self.ops.append(f"{x:.3f} {y:.3f} {w:.3f} {h:.3f} re S")

    def circle(self, x, y, r, fill=False):
        k = 0.5523 * r
        self.ops.append(f"{x + r:.3f} {y:.3f} m "
                        f"{x + r:.3f} {y + k:.3f} {x + k:.3f} {y + r:.3f} {x:.3f} {y + r:.3f} c "
                        f"{x - k:.3f} {y + r:.3f} {x - r:.3f} {y + k:.3f} {x - r:.3f} {y:.3f} c "
                        f"{x - r:.3f} {y - k:.3f} {x - k:.3f} {y - r:.3f} {x:.3f} {y - r:.3f} c "
                        f"{x + k:.3f} {y - r:.3f} {x + r:.3f} {y - k:.3f} {x + r:.3f} {y:.3f} c " + ("f" if fill else "S"))

    def text(self, x, y, s, size=3.5, bold=False, center=False):
        if center:                   # Helvetica averages ~0.5 em per character: close enough for labels
            x -= len(s) * size * 0.27
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append(f"BT /{'F2' if bold else 'F1'} {size:.2f} Tf {x:.3f} {y:.3f} Td ({s}) Tj ET")

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("latin-1")


def draw_sheet(sheet: str) -> Page:
    p = Page()
    dots = sheet_dots(sheet)
    first = 1 if sheet == "A" else 14
    p.text(15, 262, f"FeT Monitor accuracy test - sheet {sheet} of 2 (shots {first}-{first + len(dots) - 1})", 6, bold=True)
    p.text(15, 255, "Print at ACTUAL SIZE (100 %, no 'fit to page'). Check: the scale bar below must measure exactly 100 mm.", 3.3)
    p.text(15, 250.5, "Rifle faceplate, opening 50 mm. Tape the sheet over the faceplate: dashed circle over the opening, lines level.", 3.3)
    p.text(15, 246, "Shoot ONE shot at each dot, in the numbered order (see the key). Then scan the sheet flat at 600 dpi.", 3.3)

    # centre lines across the page (to level the sheet with the faceplate edges), with 10 mm ticks for scale
    p.width(0.15)
    p.gray(0.45)
    p.line(10, CY, PAGE_W - 10, CY)
    p.line(CX, 60, CX, 236)
    for d in range(-90, 91, 10):
        if d and abs(d) <= 95 and 10 <= CX + d <= PAGE_W - 10:
            p.line(CX + d, CY - 1.5, CX + d, CY + 1.5)
        if d and 60 <= CY + d <= 236:
            p.line(CX - 1.5, CY + d, CX + 1.5, CY + d)
    # the faceplate opening (dashed) and the 1-ring of the 10 m air rifle target (45.5 mm), for orientation
    p.dash(1.2, 1.2)
    p.width(0.25)
    p.circle(CX, CY, APERTURE / 2)
    p.dash()
    p.width(0.12)
    p.gray(0.7)
    p.circle(CX, CY, 45.5 / 2)
    # aiming dots: this sheet's dots black; the other sheet's positions as faint rings (never shoot them)
    mine = {rc for rc, _ in dots}
    for rc, (x, y) in sorted(grid().items()):
        if rc in mine:
            p.gray(0)
            p.circle(CX + x, CY + y, DOT / 2, fill=True)
        else:
            p.gray(0.75)
            p.width(0.12)
            p.circle(CX + x, CY + y, 0.5)
    p.gray(0)

    # the numbered key, enlarged, to the right
    kx, ky, half = 177, 196, 2 * PITCH * KEY_SCALE + 5          # upper right, clear of the centre lines
    p.text(kx - half, ky + half + 2, "Shooting order (enlarged):", 3.2, bold=True)
    p.width(0.15)
    p.gray(0.6)
    p.rect(kx - half, ky - half, 2 * half, 2 * half)
    p.gray(0)
    for n, (rc, (x, y)) in enumerate(dots, start=first):
        p.circle(kx + x * KEY_SCALE, ky + y * KEY_SCALE, 0.6, fill=True)
        p.text(kx + x * KEY_SCALE + 0.9, ky + y * KEY_SCALE + 0.6, str(n), 2.6)

    # scale checks: 100 mm bar and a 50 mm square
    p.width(0.3)
    p.line(15, 40, 115, 40)
    for d in range(0, 101, 10):
        p.line(15 + d, 40, 15 + d, 42.5 if d % 50 else 44)
    p.text(15, 46, "100 mm", 3.2)
    p.rect(150, 18, 50, 50)
    p.text(152, 70, "50 x 50 mm", 3.2)

    # what to write down
    p.text(15, 30, "Date / time: ____________________   Temperature: ______   Target name: ______________", 3.3)
    p.text(15, 23, "Shots that went wrong (number + what happened): ________________________________________", 3.3)
    p.text(15, 12, "Ground truth for the FeT Monitor target firmware. Faceplate drawing FET-016 (freETarget, MIT).", 2.6)
    return p


def write_pdf(pages, out: Path):
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for pg in pages:
        data = pg.stream()
        objs.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        content = len(objs)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                    b"/Contents %d 0 R >>" % content)
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out_b = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out_b))
        out_b += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out_b)
    out_b += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out_b += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out_b += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(out_b))


def main(argv) -> int:
    out = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent / "dist" / "aiming-sheet.pdf"
    write_pdf([draw_sheet("A"), draw_sheet("B")], out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
