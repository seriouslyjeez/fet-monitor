#!/usr/bin/env python3
"""Find each shot's hole in photos of the frame cards (tools/aiming_sheet.py card), in mm from the window centre.

    tools/accuracy/measure.py OUTDIR card1-photo1 ... card1-photo6 NEWCARD card2-photo1 ...

One photo after EACH shot, with all twelve reference dots in view (they sit under the mask: take the card out, lay
it flat, photograph straight down, put it back exactly). Put NEWCARD between the photos of different cards. Each photo's new hole = what opened since the previous photo of that card.
Writes OUTDIR/holes.json (one record per photo, in order) and OUTDIR/<photo>_rect.png (the rectified card, 40 px/mm,
the new hole outlined green and the fitted pellet disc cyan): look at every one before trusting the numbers.
Needs: numpy, opencv-python, pillow, pillow-heif (iPhone HEIC photos). See tools/accuracy/README.md.
"""
import itertools
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from pillow_heif import register_heif_opener

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiming_sheet import AIM_R, REF_N, REF_R, WIN_D   # noqa: E402  the card's own geometry

register_heif_opener()
PPM, HALF = 40.0, 24.0                       # rectified image: 40 px per mm, +-24 mm around the window centre
PAPER_DIST = 45.0                            # Lab distance from the paper's colour still counted as paper
PELLET_R = 2.0                               # 4.5 mm pellet: the hole's radius in card stock, about

def load(path):
    return cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)

def dark_blobs(img, vmax=90):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = (hsv[:, :, 2] < vmax).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    return st, cen

def find_refs(img):
    st, cen = dark_blobs(img)
    # reference dots: small, round, filled. Their size in pixels is unknown (camera distance): take round filled
    # blobs, then the twelve that sit evenly on one circle.
    cand = [cen[i] for i in range(1, len(st)) if 40 <= st[i, 4] <= 6000 and 0.8 < st[i, 2] / st[i, 3] < 1.25
            and st[i, 4] / (st[i, 2] * st[i, 3]) > 0.7]
    cand = np.array(cand)
    def pattern(on, ux, uy):          # how many points sit where reference dots go (every 360/REF_N deg, card upright)
        ang = np.degrees(np.arctan2(-(cand[on, 1] - uy), cand[on, 0] - ux)) % 360
        step = 360 / REF_N
        return int((np.abs(((ang + step / 2) % step) - step / 2) < 6).sum())
    best = None
    for a, b, c in itertools.combinations(range(len(cand)), 3):
        p1, p2, p3 = cand[a], cand[b], cand[c]
        d = 2 * (p1[0] * (p2[1] - p3[1]) + p2[0] * (p3[1] - p1[1]) + p3[0] * (p1[1] - p2[1]))
        if abs(d) < 1e-6: continue
        ux = ((p1 @ p1) * (p2[1] - p3[1]) + (p2 @ p2) * (p3[1] - p1[1]) + (p3 @ p3) * (p1[1] - p2[1])) / d
        uy = ((p1 @ p1) * (p3[0] - p2[0]) + (p2 @ p2) * (p1[0] - p3[0]) + (p3 @ p3) * (p2[0] - p1[0])) / d
        r = math.hypot(p1[0] - ux, p1[1] - uy)
        if r < 30 or r > 0.25 * max(img.shape[:2]): continue      # a real circle: inside the photo
        on = np.where(np.abs(np.hypot(cand[:, 0] - ux, cand[:, 1] - uy) - r) < 0.03 * r)[0]
        if len(on) < 6: continue
        sc = pattern(on, ux, uy)
        if best is None or sc > best[4]:
            best = (on, ux, uy, r, sc)
        if best[4] >= REF_N - 2: break
    on, ux, uy, r, _ = best
    pts = cand[on]
    ang = np.degrees(np.arctan2(-(pts[:, 1] - uy), pts[:, 0] - ux)) % 360       # image y is down
    src, dst = [], []
    for p, a in zip(pts, ang):
        k = round(a / (360 / REF_N)) % REF_N
        if abs(((a - 360 / REF_N * k + 180) % 360) - 180) < 8:                              # within 8 deg of a dot's place
            t = math.radians(360 / REF_N * k)
            src.append(p); dst.append((REF_R * math.cos(t), REF_R * math.sin(t)))
    H, _ = cv2.findHomography(np.array(src, np.float64), np.array(dst, np.float64), 0)
    res = cv2.perspectiveTransform(np.array(src, np.float64)[None], H)[0] - np.array(dst)
    return H, len(src), float(np.sqrt((res ** 2).sum(1).mean())), r / REF_R

def rectify(img, H):
    """The card around the window, card mm -> pixels: x right, y up."""
    S = np.array([[PPM, 0, HALF * PPM], [0, -PPM, HALF * PPM], [0, 0, 1]])
    size = int(2 * HALF * PPM)
    return cv2.warpPerspective(img, S @ H, (size, size), flags=cv2.INTER_LINEAR)

def to_mm(px, py):
    return (px / PPM - HALF, HALF - py / PPM)

def ink_mask():
    """Everything printed near the window (from the card design), widened: never a hole."""
    size = int(2 * HALF * PPM)
    m = np.zeros((size, size), np.uint8)
    P = lambda x, y: (int(round((x + HALF) * PPM)), int(round((HALF - y) * PPM)))
    cv2.line(m, P(-HALF, 0), P(HALF, 0), 255, 12); cv2.line(m, P(0, -HALF), P(0, HALF), 255, 12)
    cv2.circle(m, P(0, 0), int(WIN_D / 2 * PPM), 255, 10)
    marks = [(0.0, 0.0)] + [(AIM_R * math.cos(math.radians(90 - 60 * k)), AIM_R * math.sin(math.radians(90 - 60 * k))) for k in range(6)]
    for x, y in marks:
        cv2.circle(m, P(x, y), int(1.5 * PPM), 255, 10)
    for k in range(REF_N):
        t = math.radians(360 / REF_N * k); cv2.circle(m, P(REF_R * math.cos(t), REF_R * math.sin(t)), int(1.2 * PPM), 255, -1)
    return m

def hole_mask(rect):
    # paper = close to the card's own colour (any colour: red card stock, green paper, ...); the typical colour of
    # the window area is the paper's, as holes and ink cover only a small part of it
    lab = cv2.cvtColor(rect, cv2.COLOR_BGR2LAB).astype(np.float32)
    yy, xx = np.mgrid[0:rect.shape[0], 0:rect.shape[1]]
    inside = np.hypot(xx / PPM - HALF, HALF - yy / PPM) < 19.0
    ref = np.median(lab[inside], axis=0)
    paper = np.linalg.norm(lab - ref, axis=2) < PAPER_DIST
    m = (~paper).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(2.6 * PPM), int(2.6 * PPM)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, disk)       # lines, ring outlines, the 1.6 mm dots: gone; holes stay
    yy, xx = np.mgrid[0:m.shape[0], 0:m.shape[1]]
    m[np.hypot(xx / PPM - HALF, HALF - yy / PPM) > 19.0] = 0                  # the window (+ its placement)
    return m

def main(argv):
    if len(argv) < 2 or argv[0].startswith("-"):
        raise SystemExit(__doc__)
    outdir = Path(argv[0])
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    prev = None
    for path in argv[1:]:
        if path == "NEWCARD":
            prev = None; continue
        img = load(path)
        print(path.split("/")[-1], file=sys.stderr)
        H, n, rms, scale = find_refs(img)
        rect = rectify(img, H)
        m = hole_mask(rect)
        new = m if prev is None else cv2.bitwise_and(m, cv2.bitwise_not(cv2.dilate(prev, np.ones((9, 9), np.uint8))))
        new = cv2.morphologyEx(new, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
        cnts, _ = cv2.findContours(new, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        # the hole's centre: where a pellet-hole disc fits best (most newly opened area, nothing over paper), so a
        # hole that overlaps an earlier one is not just its sliver (or the earlier hole too)
        R = int(PELLET_R * PPM)
        disk = np.zeros((2 * R + 1, 2 * R + 1), np.float32)
        cv2.circle(disk, (R, R), R, 1.0, -1)
        score = cv2.filter2D((new > 0).astype(np.float32), -1, disk) - 3.0 * cv2.filter2D((m == 0).astype(np.float32), -1, disk)
        near = cv2.dilate(new, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(3 * PPM), int(3 * PPM)))) > 0
        score[~near] = -1e9                                   # only around what opened in this shot
        fy, fx = np.unravel_index(int(np.argmax(score)), score.shape)
        rec = {"photo": path.split("/")[-1], "refs": n, "ref_rms_mm": round(rms, 3), "px_per_mm_photo": round(scale, 1)}
        if cnts:
            c = cnts[0]
            mo = cv2.moments(c)
            cx, cy = mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]
            area_mm2 = cv2.contourArea(c) / PPM ** 2
            (ex, ey), er = cv2.minEnclosingCircle(c)
            rec.update(hole=[round(v, 3) for v in to_mm(fx, fy)], centroid=[round(v, 3) for v in to_mm(cx, cy)],
                       new_area_mm2=round(area_mm2, 2),
                       others=[round(cv2.contourArea(x) / PPM ** 2, 2) for x in cnts[1:4]])
        out.append(rec)
        vis = rect.copy()
        if cnts:
            cv2.drawContours(vis, [cnts[0]], -1, (0, 255, 0), 3)
            cv2.circle(vis, (int(fx), int(fy)), R, (255, 255, 0), 3)
        cv2.imwrite(str(outdir / (Path(path).stem + "_rect.png")), vis)
        prev = m if prev is None else cv2.bitwise_or(prev, m)
    (outdir / "holes.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(out)} photos -> {outdir / 'holes.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
