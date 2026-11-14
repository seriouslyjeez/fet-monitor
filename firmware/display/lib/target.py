# FeT Display's screens for its target (from the view FeT Monitor sends; nothing is scored here): pairing, and the
# target: the face with the shots, the last score, the total, the mode and the match clock.
from ili9341 import H, W, Strip, rgb
from text import big
import ui

WHITE, BLACK = rgb(255, 255, 255), rgb(0, 0, 0)
SHOT, SHOT_LAST, SHOT_EDGE = rgb(40, 140, 255), rgb(255, 50, 50), rgb(255, 255, 255)
FACE, FX, FY, STRIP_H = 208, 4, 30, 16             # the face: 208 px square at (4, 30), drawn 16 rows at a time
PX = FX + FACE + 6                                 # the panel on the right: x 218..319
PW = W - PX
ZOOMS = ("full", "bull", "auto")                   # the BOOT button cycles through them (main.py)
AUTO_START = 8                                     # Auto never zooms closer than the 8 ring (as the phone)
MODES = {"practice": "PRACTICE", "sighter": "SIGHTERS", "record": "RECORD", "ammo": "AMMO TEST"}


def pairing(s, code, footer):
    s.fill(0, 0, W, H, ui.BG)
    forget()
    big(s, "PAIR THIS DISPLAY", (W - 17 * 16) // 2, 10, 2, ui.WARN, ui.BG)
    y = 40
    for line in ("In FeT Monitor, open", "Range Control > Lane displays", "and type this code:"):
        big(s, line, (W - 8 * len(line)) // 2, y, 1, ui.FG, ui.BG)
        y += 14
    big(s, code, (W - 40 * len(code)) // 2, 96, 5, ui.GOOD, ui.BG)
    big(s, "The code lasts 10 minutes:", (W - 26 * 8) // 2, 152, 1, ui.DIM, ui.BG)
    big(s, "a new one comes by itself.", (W - 26 * 8) // 2, 166, 1, ui.DIM, ui.BG)
    big(s, footer[:40], 0, H - 8, 1, ui.DIM, ui.BG)


_drawn = {}                  # each text field on the screen: key -> (text, colour, x, y, scale, width)


def forget(y0=0, y1=H):
    """The screen (or rows y0..y1) was cleared: no text field there is drawn any more."""
    for k in [k for k, d in _drawn.items() if y0 <= d[3] < y1]:
        del _drawn[k]


def _text(s, text, x, y, scale, colour, width, key=None):
    """A text field: text in a box width wide (cut to fit), the rest of the box empty. The font is fixed-width, so a
    field that showed something before at the same place, size and colour draws only the characters that changed
    (each with its own background: nothing is cleared first, nothing flashes)."""
    cw = 8 * scale
    text = text[:max(0, width // cw)]
    key = key or (x, y)
    old = _drawn.get(key)
    if old and old[1:5] == (colour, x, y, scale):
        ot = old[0]
        for i in range(max(len(ot), len(text))):
            a = ot[i] if i < len(ot) else None
            b = text[i] if i < len(text) else None
            if a != b:
                if b is None:
                    s.fill(x + i * cw, y, cw, cw, ui.BG)
                else:
                    big(s, b, x + i * cw, y, scale, colour, ui.BG)
    else:
        if old:                                    # another size, colour or place: the old box goes first
            s.fill(old[2], old[3], old[5], 8 * old[4], ui.BG)
        if text:
            big(s, text, x, y, scale, colour, ui.BG)
        if width > len(text) * cw:
            s.fill(x + len(text) * cw, y, width - len(text) * cw, cw, ui.BG)
    _drawn[key] = (text, colour, x, y, scale, width)


def _ring_mm(f):
    """One ring's width, mm (the phone's ring_mm; worked out from the rings for an older FeT Monitor)."""
    if f.get("ring_mm"):
        return f["ring_mm"]
    d = {r["value"]: r["dia_mm"] for r in f["rings"]}
    if 8 in d and 9 in d:
        return (d[8] - d[9]) / 2
    return (max(d.values()) - min(d.values())) / 2 / max(1, len(d) - 1)


def half_mm(v, zoom):
    """Half the width of what the face area shows, in mm: the phone's fullHalf, zoomHalf and autoRing."""
    f = v["face"]
    rr = {r["value"]: r["dia_mm"] / 2 for r in f["rings"]}
    step = _ring_mm(f)
    full = max(rr.values()) + 1.3 * step
    if zoom == "bull":
        return f["black_mm"] / 2 + 0.6 * step if f.get("black_mm") else full
    if zoom == "auto":
        pr = (f.get("projectile_mm") or 4.5) / 2
        ext = 0
        for sh in v["shots"]:
            if not sh["miss"] and sh["x_h"] is not None:
                ext = max(ext, ((sh["x_h"] / 100) ** 2 + (sh["y_h"] / 100) ** 2) ** 0.5 + pr)
        for k in range(min(AUTO_START, 9), min(rr) - 1, -1):
            if k in rr and rr[k] >= ext:
                return rr[k] + 0.6 * step
    return full


def face(s, v, zoom="full"):
    """The face and the current bull's shots (the last one red), in strips, at this zoom (ZOOMS)."""
    f = v["face"]
    rings = sorted(f["rings"], key=lambda r: -r["dia_mm"])
    k = (FACE / 2) / half_mm(v, zoom)              # mm -> px
    c = FACE // 2
    black = f.get("black_mm") or 0
    shots = [sh for sh in v["shots"] if not sh["miss"] and sh["x_h"] is not None]
    pr = max(2, int((f.get("projectile_mm") or 4.5) / 2 * k + 0.5))
    st = Strip(FACE, STRIP_H)
    for y0 in range(0, FACE, STRIP_H):
        cy = c - y0
        st.fill(WHITE)
        if black:
            rb = int(black / 2 * k + 0.5)
            st.ellipse(c, cy, rb, rb, BLACK, True)
        for r in rings:
            rr = max(1, int(r["dia_mm"] / 2 * k + 0.5))
            inside = r["dia_mm"] <= black + 0.001
            if f.get("white_ten") and r["value"] == 10:
                st.ellipse(c, cy, rr, rr, WHITE, True)
            else:
                st.ellipse(c, cy, rr, rr, WHITE if inside else BLACK, False)
        for i, sh in enumerate(shots):
            x = c + int(sh["x_h"] / 100 * k + 0.5)
            y = cy - int(sh["y_h"] / 100 * k + 0.5)
            last = i == len(shots) - 1
            st.ellipse(x, y, pr, pr, SHOT_LAST if last else SHOT, True)
            st.ellipse(x, y, pr, pr, SHOT_EDGE, False)
        s.show(st, FX, FY + y0)


def _top(v):
    t = v["target"]
    return (("LANE %s" % t["lane"]) if t.get("lane") is not None else t["name"], (v.get("athlete") or {}).get("public_name") or "",
            (v.get("claim") or {}).get("public_name"))


def _face_key(v):
    return (v["face"]["id"], [(sh["n"], sh["x_h"], sh["y_h"], sh["miss"]) for sh in v["shots"]])


def _panel_key(v, problem):
    return (v["mode"], v.get("bull"), v.get("bulls"), v.get("last"), v["totals"], v["target"]["online"], problem,
            {k: x for k, x in (v.get("match") or {}).items() if k not in ("remaining_ms", "ends_at_ms")})


def top(s, v):
    """The top bar: the lane, and who is signed in (a claim draws its own band there: claim_band)."""
    title, who, claim = _top(v)
    s.fill(0, 0, W, FY - 2, ui.BG)
    forget(0, FY)
    _text(s, title, 4, 6, 2, ui.FG, 104)
    if not claim:
        _text(s, who, 112, 6, 2, ui.GOOD, W - 116)


def target(s, v, problem=None, prev=None, zoom="full"):
    """The target screen. prev: (view, problem, zoom) drawn before on this screen: only what changed is drawn again."""
    full = prev is None
    if full:
        s.fill(0, 0, W, H, ui.BG)
        forget()
    pv, pp, pz = (tuple(prev) + ("full",))[:3] if prev else (None, None, None)
    if full or _top(pv) != _top(v):
        top(s, v)
    if full or _face_key(pv) != _face_key(v) or pz != zoom:
        face(s, v, zoom)
    if full or _panel_key(pv, pp) != _panel_key(v, problem):
        panel(s, v, problem)
        return True
    return False


def panel(s, v, problem=None):
    y = FY
    mode = MODES.get(v["mode"], v["mode"].upper())
    if v.get("bulls", 1) > 1:
        mode += " %d/%d" % (v["bull"], v["bulls"])
    _text(s, mode, PX, y, 1, ui.DIM, PW)
    last = v.get("last")
    _text(s, "LAST" + (" #%d" % last["n"] if last else ""), PX, y + 16, 1, ui.DIM, PW)
    score = last["score"] if last else "-"
    colour = ui.BAD if last and last["miss"] else ui.WARN if last and last["inner"] else ui.FG
    _text(s, score, PX, y + 28, 3 if len(score) * 24 <= PW else 2, colour, PW)
    tot = v["totals"]
    _text(s, "TOTAL", PX, y + 62, 1, ui.DIM, PW)
    _text(s, tot["score"], PX, y + 74, 2 if len(tot["score"]) * 16 <= PW else 1, ui.FG, PW)
    _text(s, "%d shots" % tot["shots"], PX, y + 94, 1, ui.DIM, PW)
    _text(s, ("%d X" % tot["x"]) + ("  %d M" % tot["misses"] if tot["misses"] else ""), PX, y + 106, 1, ui.DIM, PW)
    clock(s, v)
    _text(s, (problem or "TARGET SILENT") if problem or not v["target"]["online"] else "", PX, FY + 152, 1, ui.BAD, PW)


def clock(s, v, now_server_ms=None):
    """The match clock (counted here from ends_at_ms; FeT Monitor's time is now_server_ms)."""
    m = v.get("match")
    y = FY + 124
    if not m:
        s.fill(PX, y, PW, 40, ui.BG)
        return
    _text(s, (m.get("label") or m.get("state") or "")[: PW // 8], PX, y, 1, ui.DIM, PW)
    if m.get("state") == "running" and m.get("ends_at_ms") and now_server_ms:
        left = max(0, (m["ends_at_ms"] - now_server_ms) // 1000)
    else:
        left = max(0, (m.get("remaining_ms") or 0) // 1000)
    text = "%d:%02d" % (left // 60, left % 60) if left < 6000 else "%d h" % (left // 3600)
    _text(s, text, PX, y + 12, 2, ui.WARN if m.get("state") == "running" else ui.DIM, PW)


# ------------------------------------------------------------------------------------------------ buttons, sign-in
PRESSED = rgb(110, 120, 140)


def label_lines(label, w, h):
    """(scale, lines) for a button's label: the large size on one line, else on two (whole words), else small."""
    for sc in (2, 1):
        per = (w - 2) // (8 * sc)
        if len(label) <= per:
            return sc, [label]
        lines = ui.wrap(label, per)
        if len(lines) == 2 and all(any(word in ln.split(" ") for ln in lines) for word in label.split(" ")) \
                and 2 * 8 * sc + 4 <= h - 4:
            return sc, lines
    return 1, [label[: (w - 2) // 8]]


def button(s, x, y, w, h, label, key, colour=None):
    """A button (at least 44 px tall: a finger); returns (x, y, w, h, key, label, colour) for tapping and lighting up."""
    c = colour if colour is not None else ui.BTN
    s.fill(x, y, w, h, c)
    sc, lines = label_lines(label, w, h)
    ty = y + (h - (len(lines) * 8 * sc + (len(lines) - 1) * 4)) // 2
    for ln in lines:
        big(s, ln, x + max(1, (w - 8 * sc * len(ln)) // 2), ty, sc, ui.FG, c)
        ty += 8 * sc + 4
    return (x, y, w, h, key, label, c)


def press(s, b, down):
    """Light a button up while a finger is on it (down), or draw it as it was."""
    button(s, b[0], b[1], b[2], b[3], b[5], b[4], PRESSED if down else b[6])


def panel_button(v):
    """What the panel's button does now: a match button, Not you?, sign out, sign in, or nothing."""
    m = v.get("match")
    if m and m.get("buttons"):
        b = m["buttons"][0]
        return ("START RECORD" if b == "start_record" else "SIGHTERS", b)
    if v.get("claim"):
        return ("NOT YOU?", "claim_cancel")
    if v.get("athlete"):
        return ("SIGN OUT", "signout?") if v.get("can_sign_out") else None
    if v.get("signin_url") or v.get("pin_signin"):
        return ("SIGN IN", "signin_screen")
    return None


def target_with_button(s, v, problem=None, prev=None, buttons=None, zoom="full"):
    """The target screen and its panel button; prev/buttons: what was drawn before (only changes are drawn)."""
    panel_drawn = target(s, v, problem, prev, zoom)
    pb = panel_button(v)
    if not panel_drawn and prev is not None and buttons is not None and pb == panel_button(prev[0]):
        return buttons
    s.fill(PX, H - 46, PW, 46, ui.BG)
    return [button(s, PX, H - 46, PW - 4, 44, pb[0], pb[1], ui.GOOD if pb[1] == "start_record" else None)] if pb else []


def claim_band(s, v, now_server_ms):
    """A poster's claim waiting for the first shot: who, and the time left (counted here)."""
    c = v.get("claim")
    if not c:
        return
    left = max(0, (c["until_ms"] - now_server_ms) // 1000) if now_server_ms else c["left_ms"] // 1000
    _text(s, "%s: SHOOT %d:%02d" % (c["public_name"], left // 60, left % 60), 112, 6, 1, ui.WARN, W - 116, "claim1")
    _text(s, "to claim this lane", 112, 16, 1, ui.DIM, W - 116, "claim2")


def qr_draw(s, rows, x, y, px):
    """A QR code (qr.encode) with px-pixel modules and a 4-module light border, at (x, y)."""
    n = len(rows) + 8
    w = n * px
    dark, light = (BLACK & 0xFF, BLACK >> 8), (WHITE & 0xFF, WHITE >> 8)
    s.fill(x, y, w, 4 * px, WHITE)
    s.fill(x, y + w - 4 * px, w, 4 * px, WHITE)
    line = bytearray(w * 2 * px)
    for r, row in enumerate(rows):
        i = 0
        for c in range(n):
            on = 4 <= c < n - 4 and row[c - 4]
            b = dark if on else light
            for _ in range(px):
                line[i], line[i + 1] = b
                i += 2
        for k in range(1, px):
            line[k * w * 2:(k + 1) * w * 2] = line[:w * 2]
        s.blit(x, y + (4 + r) * px, w, px, line)


def signin_screen(s, v, rows):
    """Nobody signed in: the lane's QR code (rows from qr.encode, or None), number + PIN, or shoot without."""
    s.fill(0, 0, W, H, ui.BG)
    forget()
    out = []
    if rows:
        px = max(1, min(6, (H - 16) // (len(rows) + 8)))
        size = (len(rows) + 8) * px
        qr_draw(s, rows, 6, (H - size) // 2, px)
        x = size + 14
    else:
        x = 10
    w = W - x - 6
    t = v["target"]
    _text(s, ("LANE %s" % t["lane"]) if t.get("lane") is not None else t["name"], x, 10, 2, ui.FG, w)
    y = 36
    for line in (["Scan to sign in", "with deepTENS:", "your shots go", "into your", "history."] if rows else ["Sign in with", "your athlete", "number and PIN."]):
        _text(s, line, x, y, 1, ui.DIM, w)
        y += 12
    if v.get("pin_signin"):
        out.append(button(s, x, H - 112, w, 48, "NUMBER + PIN", "keypad"))
    out.append(button(s, x, H - 56, w, 48, "SHOOT", "dismiss"))
    return out


KEYS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "DEL", "0", "OK")


KP_GAP, KP_TOP, KP_H = 3, 52, 44
KP_W = (W - 4 * KP_GAP) // 3


def _kp_xy(i):
    return KP_GAP + (i % 3) * (KP_W + KP_GAP), KP_TOP + (i // 3) * (KP_H + KP_GAP)


def keypad_top(s, phase, digits, msg=None):
    """The keypad's top lines: the title (or a message) and the digits (the PIN as stars)."""
    if msg:                                        # one field: a message takes the title's place
        _text(s, msg, 6, 6, 1, ui.BAD if msg != "Signing in..." else ui.DIM, W - 70, "kptitle")
    else:
        _text(s, "ATHLETE NUMBER" if phase == "number" else "PIN", 6, 2, 2, ui.FG, W - 70, "kptitle")
    shown = digits if phase == "number" else "*" * len(digits)
    _text(s, shown or "-", 6, 22, 3, ui.GOOD, W - 70)


def keypad_ok(s, phase, digits):
    """The key in the corner: NEXT (6 digits) or SIGN IN (a PIN of 4 to 6), usable or not."""
    ready = (len(digits) == 6) if phase == "number" else (4 <= len(digits) <= 6)
    x, y = _kp_xy(len(KEYS) - 1)
    return button(s, x, y, KP_W, KP_H, "NEXT" if phase == "number" else "SIGN IN", "ok" if ready else "", ui.GOOD if ready else ui.BTN)


def keypad_screen(s, phase, digits, msg=None):
    """The whole keypad, drawn once: athlete number (6 digits), then the PIN (4 to 6). Keys 44 px tall below the top
    52 px. Then keypad_top and keypad_ok redraw only what a key press changes."""
    s.fill(0, 0, W, H, ui.BG)
    forget()
    keypad_top(s, phase, digits, msg)
    out = [button(s, W - 58, 2, 54, 46, "X", "cancel")]
    for i, k in enumerate(KEYS[:-1]):
        x, y = _kp_xy(i)
        out.append(button(s, x, y, KP_W, KP_H, k, "k" + k))
    out.append(keypad_ok(s, phase, digits))
    return out


def confirm_screen(s, question, yes_label, yes_key):
    s.fill(0, 0, W, H, ui.BG)
    forget()
    y = 50
    for line in ui.wrap(question, W // 16):
        big(s, line, (W - 16 * len(line)) // 2, y, 2, ui.FG, ui.BG)
        y += 24
    return [button(s, 10, H - 64, (W - 30) // 2, 54, yes_label, yes_key, ui.BAD),
            button(s, 20 + (W - 30) // 2, H - 64, (W - 30) // 2, 54, "NO", "no")]


def message(s, text, colour=None):
    """An answer to a button, across the top bar (two lines at most) until top() draws it again."""
    s.fill(0, 0, W, FY - 2, ui.BG)
    forget(0, FY)
    for i, line in enumerate(ui.wrap(text, W // 8 - 1)[:2]):
        big(s, line, 4, 3 + 12 * i, 1, colour if colour is not None else ui.WARN, ui.BG)
