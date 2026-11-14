# FeT Display in a portable kit: the target's shots placed and scored here, with fetcore (the same code, the same bits
# as FeT Monitor and deepTENS: tests/test_micropython.py), and the view the screens draw, in the same shape as the views
# FeT Monitor and deepTENS send (docs/PROTOCOL.md "Lane displays"). Practice only (user 2026-11-14). deepTENS scores
# the same lines again when the display forwards them: that is the record.
#
# Memory: running totals and the current series of ten only, never every shot. A practice session is the target's
# current start (boot), cut where it was silent for SESSION_GAP_MS.
import hashlib

from fetcore import faces as F
from fetcore.lineparse import parse_shot_line
from fetcore.shotsolve import Geometry, solve
from fetcore.targetproto import parse_hello, parse_shot

SERIES = 10
SESSION_GAP_MS = 30 * 60 * 1000
ESP32_REV = 500


def _geometry(rev):
    """As FeT Monitor's default (fetserver/core.py _geometry): ESP32 boards (revision 500 and up) 10 MHz, else the Mega."""
    if rev is not None and rev >= ESP32_REV:
        return Geometry.from_upstream({"SENSOR": 232, "ANGLE": 45}, clock_mhz=10.0)
    return Geometry.from_upstream({"SENSOR": 230, "ANGLE": 45})


class KitView:
    def __init__(self, face_id="issf-ar10", name="Kit"):
        self.face = F.face(face_id)
        self.name = name
        self.device = None
        self.rev = {}                                  # device -> board revision (from its hello)
        self.reset(None, None)

    def reset(self, boot, at):
        self.boot, self.last_u = boot, at
        self.count = self.tenths = self.whole = self.x = self.misses = 0
        self.bull = []                                 # (n, x_h, y_h, miss, ring, tenths, inner)
        self.last = None

    def set_face(self, face_id):
        """Another face on the target (smallbore, say): its projectile comes with it (fetcore's faces). A series is
        scored on one face, as on FeT Monitor: a series with shots ends, a new one starts on this face."""
        if face_id == self.face.id:
            return
        self.face = F.face(face_id)
        if self.count:
            self.reset(self.boot, self.last_u)

    def line(self, rec):
        """One stored line (kit.Store's record): a hello notes the board, a shot is placed, scored and counted."""
        t, raw = rec["t"], rec.get("r")
        if rec.get("f") and rec["f"] != self.face.id and t in ("shot", "target_shot") and rec["f"] in F.FACES:
            self.set_face(rec["f"])                        # read back at a start: the face the line was shot on
        if t == "target_hello" and raw:
            try:
                self.rev[rec["d"]] = parse_hello(raw)["rev"]
            except ValueError:
                pass
            return
        if t not in ("shot", "target_shot"):
            return
        if rec["d"] != self.device or rec["b"] != self.boot or (self.last_u is not None and rec["u"] - self.last_u > SESSION_GAP_MS):
            self.device = rec["d"]
            self.reset(rec["b"], rec["u"])
        self.last_u = rec["u"]
        try:
            if t == "shot":
                sl = parse_shot_line(raw) if raw else None
                if sl is None or sl.unplaced:
                    return
                miss, x, y = sl.miss, sl.x_h, sl.y_h
            else:
                p = parse_shot(raw, None)                  # the target's key is deepTENS's: checked there
                g = _geometry(self.rev.get(rec["d"])).at_rate(p["rate_hz"])
                sol = solve(p["counts"], g, 20.0 if p["temp_c"] is None else p["temp_c"])
                if not sol.ok and sum(1 for c in p["counts"] if c is not None) >= 3:
                    return                                 # heard, but fits no point: not a shot on paper
                miss = not sol.ok
                x = None if miss else int(round(sol.x * 100))
                y = None if miss else int(round(sol.y * 100))
        except ValueError:
            return
        sc = F.MISS if miss else F.score(self.face, x, y)
        self.count += 1
        self.tenths += sc.tenths
        self.whole += sc.ring
        self.x += 1 if sc.inner else 0
        self.misses += 1 if miss else 0
        if (self.count - 1) % SERIES == 0:
            self.bull = []
        self.last = (self.count, x, y, miss, sc.ring, sc.tenths, sc.inner)
        self.bull.append(self.last)

    def view(self, online, now_ms):
        """What the screens draw (the same shape as a FeT Monitor's or deepTENS's lane display view)."""
        fv = F.view(self.face)
        whole = fv.get("decimal") is False

        def score(miss, ring, tenths):
            return "M" if miss else (str(ring) if whole else "%.1f" % (tenths / 10))
        out = {
            "v": 1, "kit": True, "state": "target", "display": {"id": 0, "name": self.name},
            "target": {"device_id": self.device or "", "name": self.device or "Waiting for the target", "lane": None, "online": online},
            "athlete": None, "signin_url": None, "pin_signin": False, "claim": None, "can_sign_out": False,
            "mode": "practice", "kind": "practice", "stream": "scored", "label": None,
            "face": {k: fv.get(k) for k in ("id", "name", "decimal", "rings", "black_mm", "white_ten", "inner_mm", "projectile_mm", "ring_mm")},
            "shots": [{"n": n, "miss": m, "inner": bool(i) and not m, "score": score(m, r, t), "x_h": x, "y_h": y}
                      for n, x, y, m, r, t, i in self.bull],
            "count": self.count, "bull": (self.count - 1) // SERIES + 1 if self.count else 1,
            "bulls": (self.count - 1) // SERIES + 1 if self.count else 1,
            "last": None if not self.last else {"n": self.last[0], "score": score(self.last[3], self.last[4], self.last[5]),
                                                "inner": bool(self.last[6]) and not self.last[3], "miss": self.last[3]},
            "totals": {"shots": self.count, "score": str(self.whole) if whole else "%.1f" % (self.tenths / 10), "x": self.x, "misses": self.misses},
            "match": None, "server_time_ms": now_ms,
        }
        key = (self.device, self.count, self.tenths, self.whole, self.x, self.misses, self.last, tuple(self.bull), self.face.id, online,
               self.name)                            # (not json of a dict: MicroPython's dicts keep no order)
        out["rev"] = hashlib.sha256(repr(key).encode()).digest().hex()[:16]
        return out


# The faces a kit offers (fetcore's, in this order, with short names for the buttons). Not the 25 m pistol face: it is
# larger than any FeT target box.
KIT_FACES = (("issf-ar10", "10 m Air Rifle"), ("issf-ap10", "10 m Air Pistol"), ("issf-r50", "50 m Rifle (A-50)"),
             ("nra-a51", "50 yd A-51"), ("nra-usas50", "50 ft USAS-50"), ("nra-a17", "50 ft A-17"),
             ("nra-a23", "50 yd A-23"), ("nra-a27", "50 yd A-27"), ("nra-a26", "50 m A-26"))


def replay(kv, path):
    """At a start: the boards from every hello in the stored lines, then the shots of the latest session only (as
    line() cuts sessions), so a restart shows what was on the screen without scoring every shot ever stored."""
    import json
    start, pos, dev, boot, last_u = 0, 0, None, None, None
    try:
        fh = open(path, "rb")
    except OSError:
        return 0
    with fh:
        for raw in fh:
            at, pos = pos, pos + len(raw)
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            t = rec.get("t")
            if t == "target_hello":
                kv.line(rec)
            elif t in ("shot", "target_shot"):
                if rec["d"] != dev or rec["b"] != boot or (last_u is not None and rec["u"] - last_u > SESSION_GAP_MS):
                    start, dev, boot = at, rec["d"], rec["b"]
                last_u = rec["u"]
        n = 0
        fh.seek(start)
        for raw in fh:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if rec.get("t") in ("shot", "target_shot"):
                kv.line(rec)
                n += 1
    return n
