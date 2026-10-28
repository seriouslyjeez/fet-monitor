"""Corrections to a match's scores by the range officials. A correction is a record (who, when, why, rule); the shot
itself is never changed, and the results show the original and the corrected value. docs/MATCH-FORMATS.md has the rules.

Kinds (per firing point, the lane number, which stays the same when a shooter is moved to another target):
  review, protest  a shot is questioned (range officer or match director); open until the match director settles it:
                   upheld (then corrects the shot), not upheld (a protest of a shot value: 2 points off that shot,
                   ISSF 6.16.5.2 c / CMP 8.4 c, suggested and confirmed by the director) or withdrawn.
  override         a shot's value replaced (original kept and shown).
  annul            a shot does not count (e.g. a disclaimed shot the range officer confirmed, ISSF 6.11.6.7).
  added            a shot added by the match director (no position on the target).
  crossfire        a shot on this firing point fired by another (ISSF 6.11.6, CMP 7.17.9, NRA 10.10.4): annulled here;
                   the firing point that fired it gets a record miss at that moment, or, if it was sighting then and the
                   shot hit a record target, 2 points off its first series (ISSF 6.11.6.3, NRA 10.10.4 a). Source
                   unknown: annulled here only.
  penalty          points off (a rule reference required), from a given shot or from the first record shot onward.
  dq               disqualified (a rule reference and a second official, CMP 7.22): no score, no place.
Any correction can be voided (kept, marked void) by the match director.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional

from .matches import MatchError, rules_of
from .scoring import is_inner_ten

KINDS = ("review", "protest", "override", "annul", "added", "crossfire", "penalty", "dq")
OPEN_KINDS = ("review", "protest")
OFFICER_KINDS = OPEN_KINDS                     # what a range officer may record; everything else is the director's
OUTCOMES = ("upheld", "not_upheld", "withdrawn")
NOT_UPHELD_RULE = {"ISSF": "ISSF 6.16.5.2 c", "CMP": "CMP 8.4 c"}      # 2 points off a protested shot not upheld
SIGHTER_CROSSFIRE_RULE = {"ISSF": "ISSF 6.11.6.3", "NRA": "NRA 10.10.4 a"}
MAX_TEXT = 300


def _text(v, what, need=False, n=MAX_TEXT):
    if v is None or (isinstance(v, str) and not v.strip()):
        if need:
            raise MatchError(f"{what} is required")
        return None
    if not isinstance(v, str):
        raise MatchError(f"{what} must be text")
    return v.strip()[:n]


def shot_value(d: dict) -> dict:
    """{value: 10.4} or {miss: true}, optional inner (else from the value) -> tenths, ring, inner, miss."""
    if d.get("miss") is True:
        return {"tenths": 0, "ring": 0, "inner": False, "miss": True}
    v = d.get("value")
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 10.9 or math.isnan(v):
        raise MatchError("a shot value is 0 to 10.9 (or a miss)")
    tenths = int(round(v * 10))
    inner = d.get("inner")
    if inner is not None and not isinstance(inner, bool):
        raise MatchError("inner must be true or false")
    return {"tenths": tenths, "ring": min(10, tenths // 10), "inner": is_inner_ten(tenths) if inner is None else (inner and tenths >= 100),
            "miss": False}


class Corrections:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.matches = core.matches

    # ------------------------------------------------------------------------------------------- recording
    def add(self, mid: int, kind: str, lane: int, by: str, director: bool, shot_id: Optional[int] = None, data: Optional[dict] = None) -> int:
        if kind not in KINDS:
            raise MatchError(f"kind must be one of {', '.join(KINDS)}")
        if kind not in OFFICER_KINDS and not director:
            raise MatchError("only a match director can do that")
        data = dict(data or {}) if isinstance(data, (dict, type(None))) else None
        if data is None:
            raise MatchError("data must be an object")
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self.matches._row(c, mid)
            if m["state"] == "ready":
                raise MatchError("the match has not started")
            self.matches.not_official(m)
            ln = c.execute("SELECT * FROM match_lanes WHERE match_id=? AND lane=?", (mid, lane)).fetchone() if isinstance(lane, int) else None
            if ln is None:
                raise MatchError("no such firing point in this match")
            shot = None
            if kind in ("review", "protest", "override", "annul", "crossfire") or (kind == "penalty" and shot_id is not None):
                shot = c.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone() if isinstance(shot_id, int) else None
                if shot is None or ln["series_id"] is None or shot["series_id"] != ln["series_id"]:
                    raise MatchError("that shot is not on this firing point")
                if kind in ("override", "annul", "crossfire") and self._active(c, mid, shot_id, ("annul", "crossfire")):
                    raise MatchError("that shot is already annulled")
            clean = {"note": _text(data.get("note"), "a note")}
            if kind in ("review", "protest"):
                pass
            elif kind == "override":
                clean.update(shot_value(data), note=_text(data.get("note"), "a reason", need=True))
            elif kind == "annul":
                clean.update(note=_text(data.get("note"), "a reason", need=True), rule=_text(data.get("rule"), "the rule", n=60))
            elif kind == "added":
                clean.update(shot_value(data), note=_text(data.get("note"), "a reason", need=True))
                clean["at_ms"] = self._added_at(c, ln, data.get("after"))
            elif kind == "crossfire":
                src = data.get("from_lane")
                if src is not None:
                    if not isinstance(src, int) or isinstance(src, bool) or src == lane or \
                            c.execute("SELECT 1 FROM match_lanes WHERE match_id=? AND lane=?", (mid, src)).fetchone() is None:
                        raise MatchError("the crossfire came from another firing point of this match (or unknown)")
                fl = c.execute("SELECT * FROM match_lanes WHERE match_id=? AND lane=?", (mid, src)).fetchone() if src is not None else None
                clean.update(from_lane=src, at_ms=shot["event_ms"], hit_stream=shot["stream"],
                             firer_stream=self._stream_at(c, fl, shot["event_ms"]) if fl is not None else None)
            elif kind == "penalty":
                pts = data.get("points")
                if not isinstance(pts, int) or isinstance(pts, bool) or not 1 <= pts <= 100:
                    raise MatchError("a penalty is 1 to 100 points")
                clean.update(points=pts, rule=_text(data.get("rule"), "a rule reference", need=True, n=60))
            elif kind == "dq":
                if self._dq(c, mid, lane):
                    raise MatchError("already disqualified")
                clean.update(rule=_text(data.get("rule"), "a rule reference", need=True, n=60),
                             official=_text(data.get("official"), "the second official's name", need=True, n=60))
            cid = c.execute("INSERT INTO shot_corrections (match_id, lane, shot_id, kind, status, data, created_by, created_ms) "
                            "VALUES (?,?,?,?,?,?,?,?)", (mid, lane, shot_id if shot is not None else None, kind,
                                                       "open" if kind in OPEN_KINDS else "done", json.dumps(clean), by, now)).lastrowid
        self.matches._pub(mid)
        return cid

    def settle(self, mid: int, corr_id: int, outcome: str, by: str, penalty: Optional[int] = None, note=None) -> None:
        """A review or protest is settled by the match director. not_upheld with penalty (points) adds that penalty on
        the shot with the rulebook's rule (ISSF 6.16.5.2 c, CMP 8.4 c)."""
        if outcome not in OUTCOMES:
            raise MatchError(f"outcome must be one of {', '.join(OUTCOMES)}")
        now = self.core.now_ms()
        with self.db.write() as c:
            self.matches.not_official(self.matches._row(c, mid))
            r = self._get(c, mid, corr_id)
            if r["kind"] not in OPEN_KINDS or r["status"] != "open":
                raise MatchError("only an open review or protest can be settled")
            data = dict(json.loads(r["data"]), outcome=outcome, settle_note=_text(note, "a note"))
            c.execute("UPDATE shot_corrections SET status='resolved', data=?, resolved_by=?, resolved_ms=? WHERE id=?",
                      (json.dumps(data), by, now, corr_id))
            if penalty:
                if outcome != "not_upheld" or r["kind"] != "protest":
                    raise MatchError("a penalty comes only with a protest that is not upheld")
                if not isinstance(penalty, int) or isinstance(penalty, bool) or not 1 <= penalty <= 100:
                    raise MatchError("a penalty is 1 to 100 points")
                rule = NOT_UPHELD_RULE.get(rules_of(json.loads(self.matches._row(c, mid)["format_json"])), "protest not upheld")
                c.execute("INSERT INTO shot_corrections (match_id, lane, shot_id, kind, status, data, created_by, created_ms) VALUES (?,?,?,?,?,?,?,?)",
                          (mid, r["lane"], r["shot_id"], "penalty", "done", json.dumps({"points": penalty, "rule": rule, "note": "protest not upheld", "protest": corr_id}), by, now))
        self.matches._pub(mid)

    def void(self, mid: int, corr_id: int, by: str) -> None:
        with self.db.write() as c:
            self.matches.not_official(self.matches._row(c, mid))
            r = self._get(c, mid, corr_id)
            if r["status"] == "void":
                raise MatchError("already void")
            c.execute("UPDATE shot_corrections SET status='void', resolved_by=?, resolved_ms=? WHERE id=?", (by, self.core.now_ms(), corr_id))
        self.matches._pub(mid)

    # ------------------------------------------------------------------------------------------- reading
    def list(self, c, mid: int) -> List[dict]:
        out = []
        for r in c.execute("SELECT * FROM shot_corrections WHERE match_id=? ORDER BY id", (mid,)):
            out.append({"id": r["id"], "lane": r["lane"], "shot_id": r["shot_id"], "kind": r["kind"], "status": r["status"],
                        "data": json.loads(r["data"]), "by": r["created_by"], "at_ms": r["created_ms"], "settled_by": r["resolved_by"]})
        return out

    def lane_shots(self, mid: int, lane: int) -> dict:
        """Every shot of a firing point (sighting and record, firing order) with what the corrections say about it,
        for the console's shot list."""
        c = self.db.conn()
        m = self.matches._row(c, mid)
        ln = c.execute("SELECT * FROM match_lanes WHERE match_id=? AND lane=?", (mid, lane)).fetchone() if isinstance(lane, int) else None
        if ln is None:
            raise MatchError("no such firing point in this match")
        corr = [x for x in self.list(c, mid) if x["lane"] == lane or x["data"].get("from_lane") == lane]
        by_shot: Dict[int, list] = {}
        for x in corr:
            if x["shot_id"] is not None and x["lane"] == lane:
                by_shot.setdefault(x["shot_id"], []).append(x)
        shots = []
        if ln["series_id"] is not None:
            for r in c.execute("SELECT id, series_no, stream, tenths, ring, is_miss, event_ms FROM shots WHERE series_id=? ORDER BY event_ms, id",
                               (ln["series_id"],)):
                shots.append({"id": r["id"], "n": r["series_no"], "stream": r["stream"], "decimal": r["tenths"] / 10.0, "ring": r["ring"],
                              "miss": bool(r["is_miss"]), "inner": not r["is_miss"] and is_inner_ten(r["tenths"]), "at_ms": r["event_ms"],
                              "corrections": by_shot.get(r["id"], [])})
        others = [{"lane": l["lane"], "competitor": l["competitor"]} for l in self.matches._lanes(c, mid) if l["lane"] != lane]
        return {"match_id": mid, "lane": lane, "competitor": ln["competitor"], "shots": shots, "others": others,
                "corrections": [x for x in corr if x["shot_id"] is None or x["lane"] != lane], "tie_rules": rules_of(json.loads(m["format_json"]))}

    def open_count(self, c, mid: int) -> int:
        return c.execute("SELECT COUNT(*) FROM shot_corrections WHERE match_id=? AND status='open'", (mid,)).fetchone()[0]

    def effects(self, c, m, lane: int) -> dict:
        """What the corrections do to one firing point: annulled shot ids, overrides by shot id, added shots (incl.
        crossfire misses), penalties, disqualification, and the corrections that touch it (for display)."""
        mid = m["id"]
        rules = rules_of(json.loads(m["format_json"]))
        out = {"annulled": {}, "overrides": {}, "added": [], "penalties": [], "dq": None}
        for r in c.execute("SELECT * FROM shot_corrections WHERE match_id=? AND status!='void' ORDER BY id", (mid,)):
            d = json.loads(r["data"])
            k = r["kind"]
            if r["lane"] == lane:
                if k in ("annul", "crossfire"):
                    out["annulled"][r["shot_id"]] = {"id": r["id"], "kind": k, "from_lane": d.get("from_lane"), "note": d.get("note")}
                elif k == "override":
                    out["overrides"][r["shot_id"]] = dict(d, id=r["id"])          # the latest wins
                elif k == "added":
                    out["added"].append({"corr": r["id"], "at_ms": d["at_ms"], "tenths": d["tenths"], "ring": d["ring"], "inner": d["inner"],
                                         "miss": d["miss"], "added": True, "note": d.get("note"), "by": r["created_by"]})
                elif k == "penalty":
                    out["penalties"].append({"corr": r["id"], "points": d["points"], "rule": d["rule"], "note": d.get("note"), "shot_id": r["shot_id"]})
                elif k == "dq":
                    out["dq"] = {"corr": r["id"], "rule": d["rule"], "official": d["official"], "note": d.get("note"), "by": r["created_by"]}
            if k == "crossfire" and d.get("from_lane") == lane:            # this firing point fired it
                if d.get("firer_stream") == "scored":
                    out["added"].append({"corr": r["id"], "at_ms": d["at_ms"], "tenths": 0, "ring": 0, "inner": False, "miss": True,
                                         "crossfire_to": r["lane"], "note": "crossfire onto firing point %d" % r["lane"]})
                elif d.get("hit_stream") == "scored" and rules in SIGHTER_CROSSFIRE_RULE:
                    out["penalties"].append({"corr": r["id"], "points": 2, "rule": SIGHTER_CROSSFIRE_RULE[rules],
                                             "note": "sighter crossfired onto the record target of firing point %d" % r["lane"], "shot_id": None})
        return out

    # ------------------------------------------------------------------------------------------- helpers
    @staticmethod
    def _get(c, mid, corr_id):
        r = c.execute("SELECT * FROM shot_corrections WHERE id=? AND match_id=?", (corr_id, mid)).fetchone() if isinstance(corr_id, int) else None
        if r is None:
            raise MatchError("no such correction")
        return r

    @staticmethod
    def _active(c, mid, shot_id, kinds) -> bool:
        q = ",".join("?" * len(kinds))
        return c.execute(f"SELECT 1 FROM shot_corrections WHERE match_id=? AND shot_id=? AND status!='void' AND kind IN ({q})",
                         (mid, shot_id, *kinds)).fetchone() is not None

    @staticmethod
    def _dq(c, mid, lane) -> bool:
        return c.execute("SELECT 1 FROM shot_corrections WHERE match_id=? AND lane=? AND kind='dq' AND status!='void'", (mid, lane)).fetchone() is not None

    @staticmethod
    def _added_at(c, ln, after) -> int:
        """In firing order: just after record shot number `after`, or (None) after the last shot of the firing point."""
        if ln["series_id"] is None:
            raise MatchError("this firing point has no shots yet")
        if after is None:
            last = c.execute("SELECT MAX(event_ms) FROM shots WHERE series_id=?", (ln["series_id"],)).fetchone()[0]
            if last is None:
                raise MatchError("this firing point has no shots yet: add it after one")
            return last + 1
        r = c.execute("SELECT event_ms FROM shots WHERE series_id=? AND stream='scored' AND series_no=?", (ln["series_id"], after)).fetchone() \
            if isinstance(after, int) and not isinstance(after, bool) else None
        if r is None:
            raise MatchError("no such record shot to add it after")
        return r["event_ms"] + 1

    @staticmethod
    def _stream_at(c, ln, t: int) -> Optional[str]:
        """Was the firing point in sighting or record at time t (from its segments)?"""
        if ln["series_id"] is None:
            return None
        g = c.execute("SELECT mode FROM segments WHERE series_id=? AND started_ms<=? AND (ended_ms IS NULL OR ended_ms>?) ORDER BY id DESC LIMIT 1",
                      (ln["series_id"], t, t)).fetchone()
        return None if g is None else ("sighter" if g["mode"] == "sighter" else "scored")
