#!/usr/bin/env python3
"""Printable aiming sheets for measuring a target's accuracy (ground truth for the target firmware work).

    python3 tools/aiming_sheet.py [out.pdf]        # default dist/aiming-sheet.pdf
    python3 tools/aiming_sheet.py card [N] [out.pdf]   # N (default 3) cut-out cards for the target frame: dist/aiming-card.pdf

A 5 x 5 grid of aiming dots at 7 mm pitch around the centre of the rifle faceplate opening (50 mm across, drawing
FET-016), split like a checkerboard over two sheets (A: 13 dots, B: 12) so that holes on one sheet are about 10 mm
apart and cannot merge. Each sheet is taped over the faceplate, shot one dot at a time in the numbered order, then
scanned; the holes are measured against the printed centre lines. US Letter, actual size, plain PDF (stdlib only).
"""
from __future__ import annotations

import math
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


# ---------------------------------------------------------------- cards for the target frame (the paper plane)
# The card replaces the target card in the frame, so the target hears shots exactly as in normal use (the first
# sheets were taped over the faceplate, which changed what the microphones heard). Measured on the user's mask:
CARD_W, CARD_H = 185.0, 229.0                   # mm
WIN_D = 30.0                                    # the mask's window
WIN_X, WIN_Y = 77.0 + WIN_D / 2, 89.0 + WIN_D / 2   # window centre from the card's left and TOP edges
AIM_R = 8.0                                     # six aiming rings around the centre one (holes stay in the window)
REF_R = 20.0                                    # twelve reference dots, hidden by the mask: never shot
REF_N = 12


def card_marks():
    """Shooting order: 1 the centre, 2-7 clockwise from 12 o'clock; (x, y) mm from the window centre, y up."""
    marks = [(0.0, 0.0)]
    for k in range(6):
        a = math.radians(90 - 60 * k)
        marks.append((AIM_R * math.cos(a), AIM_R * math.sin(a)))
    return marks


def card_refs():
    return [(REF_R * math.cos(2 * math.pi * k / REF_N), REF_R * math.sin(2 * math.pi * k / REF_N)) for k in range(REF_N)]


def draw_card(n: int, total: int) -> Page:
    p = Page()
    left, bottom = (PAGE_W - CARD_W) / 2, (PAGE_H - CARD_H) / 2
    cx, cy = left + WIN_X, bottom + CARD_H - WIN_Y
    top = bottom + CARD_H
    # outside the card: how to print and cut
    p.text(left, top + 8, f"FeT Monitor accuracy card {n} of {total} - print at ACTUAL SIZE, cut along the solid line", 3.4, bold=True)
    p.text(left, top + 3.5, "The cut card must measure 185 x 229 mm. It goes in the target frame instead of the target card.", 3.0)
    p.text(left, bottom - 6, "Ground truth for the FeT Monitor target firmware (the test of the shot positions).", 2.6)
    # the card outline and crop marks
    p.gray(0)
    p.width(0.3)
    p.rect(left, bottom, CARD_W, CARD_H)
    for x in (left, left + CARD_W):
        for y in (bottom, top):
            p.line(x - 6 if x == left else x + 1, y, x - 1 if x == left else x + 6, y)
            p.line(x, y - 6 if y == bottom else y + 1, x, y - 1 if y == bottom else y + 6)
    # centre lines to the card's edges, 10 mm ticks (5 mm inside the window), to square the card in the frame
    p.width(0.15)
    p.gray(0.35)
    p.line(left, cy, left + CARD_W, cy)
    p.line(cx, bottom, cx, top)
    for d in range(-200, 201, 5):
        if d == 0:
            continue
        big = d % 10 == 0
        if not big and abs(d) > WIN_D / 2:
            continue
        L = 1.6 if big else 1.0
        if left <= cx + d <= left + CARD_W:
            p.line(cx + d, cy - L, cx + d, cy + L)
        if bottom <= cy + d <= top:
            p.line(cx - L, cy + d, cx + L, cy + d)
    # the window (dashed), aiming rings with their numbers, reference dots behind the mask
    p.dash(1.0, 1.0)
    p.width(0.2)
    p.circle(cx, cy, WIN_D / 2)
    p.dash()
    p.gray(0)
    for i, (x, y) in enumerate(card_marks(), start=1):
        p.width(0.35)
        p.circle(cx + x, cy + y, 1.5)
        p.circle(cx + x, cy + y, 0.25, fill=True)
        p.text(cx + x + 1.8, cy + y + 1.2, str(i), 2.2)
    for (x, y) in card_refs():
        p.circle(cx + x, cy + y, DOT / 2, fill=True)
    # on the hidden parts of the card, in two columns either side of the vertical centre line:
    # left = what to do and notes, right = the shooting order and a scale check
    lx = left + 6
    p.text(lx, top - 12, "TOP - card %d of %d" % (n, total), 4.5, bold=True)
    lines = ["1. Tape it flat in the frame instead of",
             "   the target card: centre lines level,",
             "   window over the rings.",
             "2. Seven shots: 1 at the centre ring,",
             "   then 2-7 clockwise from 12 o'clock.",
             "3. After EVERY shot: a phone photo",
             "   through the window (which hole is new).",
             "4. At the end: take the card out and",
             "   photograph it whole, flat, from above.",
             "The dots around the window are the",
             "measuring marks: do not shoot at them."]
    for i, t in enumerate(lines):
        p.text(lx, top - 20 - 5 * i, t, 2.9)
    for i, t in enumerate(["Date / time: ____________________", "Temperature: ________", "Target: _______________",
                           "Shots that went wrong:", "______________________________", "______________________________"]):
        p.text(lx, bottom + 50 - 6 * i, t, 2.9)
    rx = cx + 8
    p.text(rx, top - 20, "Shooting order (3x):", 3.0, bold=True)
    kx, ky = cx + 42, top - 50
    for i, (x, y) in enumerate(card_marks(), start=1):
        p.width(0.3)
        p.circle(kx + 3 * x, ky + 3 * y, 1.6)
        p.text(kx + 3 * x + 2.5, ky + 3 * y - 1, str(i), 3.0)
    bx, by = cx + 15, bottom + 20
    p.width(0.3)
    p.line(bx, by, bx + 50, by)
    for d in range(0, 51, 10):
        p.line(bx + d, by, bx + d, by + (2.5 if d % 50 else 4))
    p.text(bx, by + 6, "50 mm: check before cutting", 2.9)
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
    dist = Path(__file__).resolve().parent.parent / "dist"
    if argv and argv[0] == "card":                      # card [how many, default 3] [out.pdf]
        rest = argv[1:]
        count = int(rest.pop(0)) if rest and rest[0].isdigit() else 3
        out = Path(rest[0]) if rest else dist / "aiming-card.pdf"
        write_pdf([draw_card(n, count) for n in range(1, count + 1)], out)
        print(out)
        return 0
    out = Path(argv[0]) if argv else dist / "aiming-sheet.pdf"
    write_pdf([draw_sheet("A"), draw_sheet("B")], out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
