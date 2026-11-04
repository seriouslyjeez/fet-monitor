"""TV displays: saved views, set up in Range Control, that a TV opens by a 5-character address (http://<pi>/<code>).

A view shows either targets (their cards and the match clock) or a scoreboard, and follows one of:
  range        whatever match is on the range now (a single match, a relay or a final);
  competition  one competition: its relays one after the other, then its finals;
  match        one match;
  none         no match: only the targets chosen (practice).
What is "now" is decided here, each time a TV asks (current): a match under way (running, paused, waiting for START,
overtime); else the next one to start (the lowest id that is ready); else the one that finished last. So a TV follows
a competition from relay to relay and into its final without anyone changing its address.

Displays are read only and public on the club LAN, like the scoreboard; only Range Control can save or delete one.
"""
from __future__ import annotations

import json
import random
import re
from typing import List, Optional

from .matches import MatchError

CODE_RE = re.compile(r"^[A-Za-z0-9]{5}$")
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # no 0/O, 1/I/L: easy to type with a TV remote
LIVE = ("running", "paused", "holding", "overtime")
SHOWS = ("targets", "board")
FOLLOWS = ("range", "competition", "match", "none")
THEMES = ("auto", "dark", "light")
RANGE_DONE_MS = 6 * 3600 * 1000                 # following the range: a match that finished in the last 6 h
MAX_DISPLAYS = 100
MAX_TARGETS = 64


def norm_code(code) -> str:
    """A display's code as stored (upper case), or MatchError: 5 letters or digits."""
    if not isinstance(code, str) or not CODE_RE.match(code.strip()):
        raise MatchError("a display's address is 5 letters or digits")
    return code.strip().upper()


class Displays:
    def __init__(self, core):
        self.core = core
        self.db = core.db

    def _pub(self, code):
        self.core.hub.publish({"type": "display", "code": code})

    # ------------------------------------------------------------------------------------------- saved views
    def _clean(self, c, data: dict) -> dict:
        """The settings of a view, checked: name, show, follow (with its competition or match), targets, theme."""
        if not isinstance(data, dict):
            raise MatchError("a display's settings are an object")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise MatchError("the display needs a name")
        show, follow, theme = data.get("show", "targets"), data.get("follow", "range"), data.get("theme", "auto")
        if show not in SHOWS:
            raise MatchError("a display shows targets or a scoreboard")
        if follow not in FOLLOWS:
            raise MatchError("a display follows the range, a competition, a match or no match")
        if theme not in THEMES:
            raise MatchError("a display's look: auto, dark or light")
        if show == "board" and follow == "none":
            raise MatchError("a scoreboard follows the range, a competition or a match")
        targets = data.get("targets") or []
        if not isinstance(targets, list) or len(targets) > MAX_TARGETS or not all(isinstance(t, str) and 0 < len(t) <= 64 for t in targets):
            raise MatchError("targets: a list of target ids")
        cfg = {"show": show, "follow": follow, "theme": theme, "targets": list(dict.fromkeys(targets)) if show == "targets" else []}
        if follow == "competition":
            cid = data.get("competition_id")
            if not isinstance(cid, int) or isinstance(cid, bool) or not c.execute("SELECT 1 FROM competitions WHERE id=?", (cid,)).fetchone():
                raise MatchError("choose the competition the display follows")
            cfg["competition_id"] = cid
        if follow == "match":
            mid = data.get("match_id")
            if not isinstance(mid, int) or isinstance(mid, bool) or not c.execute("SELECT 1 FROM matches WHERE id=?", (mid,)).fetchone():
                raise MatchError("choose the match the display follows")
            cfg["match_id"] = mid
        return {"name": name.strip()[:60], "config": cfg}

    def _new_code(self, c) -> str:
        rnd = random.SystemRandom()
        while True:
            code = "".join(rnd.choice(ALPHABET) for _ in range(5))
            if not c.execute("SELECT 1 FROM displays WHERE code=?", (code,)).fetchone():
                return code

    def save(self, data: dict, code: Optional[str] = None) -> str:
        """Create a view (code None), or change one; data["code"] gives it another address (or its first one).
        Returns its code."""
        now, old = self.core.now_ms(), None
        with self.db.write() as c:
            v = self._clean(c, data)
            want = norm_code(data["code"]) if data.get("code") else None
            if code is not None:
                code = norm_code(code)
                if not c.execute("SELECT 1 FROM displays WHERE code=?", (code,)).fetchone():
                    raise MatchError("no such display")
            elif c.execute("SELECT COUNT(*) FROM displays").fetchone()[0] >= MAX_DISPLAYS:
                raise MatchError(f"at most {MAX_DISPLAYS} displays")
            if want and want != code and c.execute("SELECT 1 FROM displays WHERE code=?", (want,)).fetchone():
                raise MatchError(f"another display has the address {want}")
            if code is None:
                code = want or self._new_code(c)
                c.execute("INSERT INTO displays (code, name, config_json, created_ms, updated_ms) VALUES (?,?,?,?,?)",
                          (code, v["name"], json.dumps(v["config"]), now, now))
            else:
                c.execute("UPDATE displays SET code=?, name=?, config_json=?, updated_ms=? WHERE code=?",
                          (want or code, v["name"], json.dumps(v["config"]), now, code))
                old, code = code, want or code
        if old is not None and old != code:
            self._pub(old)                                 # a TV on the old address learns it is gone
        self._pub(code)
        return code

    def delete(self, code: str) -> None:
        code = norm_code(code)
        with self.db.write() as c:
            if not c.execute("DELETE FROM displays WHERE code=?", (code,)).rowcount:
                raise MatchError("no such display")
        self._pub(code)

    def _view(self, r) -> dict:
        return dict(json.loads(r["config_json"]), code=r["code"], name=r["name"], updated_ms=r["updated_ms"])

    def list(self) -> List[dict]:
        """Every saved view, with what it shows now (Range Control)."""
        c = self.db.conn()
        out = []
        for r in c.execute("SELECT * FROM displays ORDER BY name COLLATE NOCASE, code").fetchall():
            v = self._view(r)
            ids = self.current(v)
            v["now"] = [{"id": m["id"], "name": m["name"], "state": m["state"]}
                        for m in (c.execute("SELECT id, name, state FROM matches WHERE id=?", (i,)).fetchone() for i in ids) if m]
            out.append(v)
        return out

    def choices(self) -> dict:
        """What a view can follow (Range Control's editor): the competitions and the matches not aborted, newest first."""
        c = self.db.conn()
        return {"competitions": [dict(r) for r in c.execute("SELECT id, name FROM competitions WHERE results_hidden=0 ORDER BY id DESC LIMIT 100")],
                "matches": [dict(r) for r in c.execute("SELECT id, name, state, competition_id, final_kind FROM matches "
                                                       "WHERE state!='aborted' AND results_hidden=0 ORDER BY id DESC LIMIT 100")]}

    # ------------------------------------------------------------------------------------------- what a TV shows now
    def current(self, v: dict) -> List[int]:
        """The matches a view shows now (ids): see the module notes."""
        c, follow = self.db.conn(), v["follow"]
        if follow == "none":
            return []
        if follow == "match":
            r = c.execute("SELECT id FROM matches WHERE id=? AND results_hidden=0", (v["match_id"],)).fetchone()
            return [r["id"]] if r else []
        if follow == "competition":
            rows = c.execute("""SELECT m.id, m.state, m.ended_ms FROM matches m JOIN competitions k ON k.id=m.competition_id
                                WHERE m.competition_id=? AND m.state!='aborted' AND m.results_hidden=0 AND k.results_hidden=0
                                ORDER BY m.id""", (v["competition_id"],)).fetchall()
        else:
            rows = c.execute("""SELECT m.id, m.state, m.ended_ms FROM matches m LEFT JOIN competitions k ON k.id=m.competition_id
                                WHERE m.state!='aborted' AND m.results_hidden=0 AND COALESCE(k.results_hidden, 0)=0
                                AND (m.state!='done' OR m.ended_ms>?) ORDER BY m.id""",
                             (self.core.now_ms() - RANGE_DONE_MS,)).fetchall()
        return pick(rows)

    def show(self, code: str) -> dict:
        """What a TV at this address draws: the view, the matches now (scoreboard entries, matches.board) and, for a
        scoreboard, each competition whose relay is on now with all its relays together (its results)."""
        code = norm_code(code)
        c = self.db.conn()
        r = c.execute("SELECT * FROM displays WHERE code=?", (code,)).fetchone()
        if r is None:
            raise MatchError("no such display")
        v = self._view(r)
        ids = self.current(v)
        matches, comps = self.core.matches.board(ids) if ids else [], []
        for b in matches:
            m = c.execute("SELECT competition_id, final_kind FROM matches WHERE id=?", (b["id"],)).fetchone()
            b["relay_of"] = m["competition_id"] if m["competition_id"] is not None and not m["final_kind"] else None
            if v["show"] == "board" and b["relay_of"] is not None and not any(x["id"] == b["relay_of"] for x in comps):
                try:
                    comps.append(dict(self.core.results.get(f"c{b['relay_of']}"), current_relay=b["id"]))
                except MatchError:
                    b["relay_of"] = None                  # its results are not public: the relay alone
        return {"view": v, "now": ids, "matches": matches, "competitions": comps, "server_time_ms": self.core.now_ms()}


def pick(rows) -> List[int]:
    """Of a list of matches (id, state, ended_ms): those under way; else the next to start; else the last finished."""
    live = [r["id"] for r in rows if r["state"] in LIVE]
    if live:
        return live
    ready = [r["id"] for r in rows if r["state"] == "ready"]
    if ready:
        return [min(ready)]
    done = [r for r in rows if r["state"] == "done"]
    return [max(done, key=lambda r: (r["ended_ms"] or 0, r["id"]))["id"]] if done else []
