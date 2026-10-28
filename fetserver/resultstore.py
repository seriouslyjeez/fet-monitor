"""Results on the public Results page, and deleting matches and results independently of each other.

A result has a key: c<id> a competition (all its relays), m<id> a single match (no competitors list), a<id> a frozen
copy. A plain number is a competition (older links). Every kind has the same shape (competitions.results()), so one
results page, print-out, score card and CSV serve them all.

Deleting results (results_hidden) takes them off the Results page, the TV scoreboard, print-outs and CSV; the match
stays in Match Control, with its shots, and the results can be shown again. A frozen copy is deleted for good.
Deleting a match or competition from Match Control first saves a frozen copy of its results (unless its results were
deleted already, or it never started), then removes the match: its clock, log, firing points, decisions and (for a
competition) the competitors list. The shots stay in each target's own history. A match that is running cannot be
deleted: stop it first.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List

from . import matchformats as mf
from . import ranking
from .matches import LIVE, MatchError, rules_of

KEY_RE = re.compile(r"^([cma]?)(\d{1,9})$")
MAX_DELETE = 200


def parse_key(key) -> tuple:
    m = KEY_RE.match(str(key)) if isinstance(key, (str, int)) else None
    if not m:
        raise MatchError(f"not a result: {key!r}")
    return (m.group(1) or "c"), int(m.group(2))


class ResultStore:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.matches = core.matches
        self.competitions = core.competitions

    # ------------------------------------------------------------------------------------------- reading
    def get(self, key, with_shots: bool = False, hidden_too: bool = False) -> dict:
        kind, n = parse_key(key)
        c = self.db.conn()
        if kind == "a":
            r = c.execute("SELECT * FROM results_archive WHERE id=?", (n,)).fetchone()
            if r is None:
                raise MatchError("no such results")
            res = json.loads(r["json"])
            if not with_shots:
                for cl in res["classes"]:
                    for e in cl["individuals"]:
                        e.pop("shot_list", None)
            return dict(res, key=f"a{n}", kind="frozen", frozen_ms=r["created_ms"], source=r["kind"])
        table = "competitions" if kind == "c" else "matches"
        r = c.execute(f"SELECT results_hidden FROM {table} WHERE id=?" + (" AND competition_id IS NULL" if kind == "m" else ""), (n,)).fetchone()
        if r is None or (r["results_hidden"] and not hidden_too):
            raise MatchError("no such results")
        res = self.competitions.results(n, with_shots) if kind == "c" else self.match_results(n, with_shots)
        return dict(res, key=f"{kind}{n}", kind="competition" if kind == "c" else "match")

    def match_results(self, mid: int, with_shots: bool = False) -> dict:
        """A single match in the shape of a competition's results: one class, no teams; a firing point without a
        competitor's name is listed by its target's name."""
        c = self.db.conn()
        m = self.matches._row(c, mid)
        fmt = json.loads(m["format_json"])
        decimal, rules = fmt.get("scoring") == "decimal", rules_of(fmt)
        flags = {f["shot_id"]: f for f in (self.matches.flags(c, m) if m["state"] != "ready" else [])}
        rows = []
        for ln in self.matches._lanes(c, mid):
            shots, x = self.matches.counted_row(c, m, ln, flags)
            t = c.execute("SELECT name FROM targets WHERE device_id=?", (ln["device_id"],)).fetchone()
            name = ln["competitor"] or (t["name"] if t and t["name"] else ln["device_id"])
            e = dict(self.matches.entry_view(shots, decimal), summary=ranking.summary(shots), sort_name=(name.split() or [""])[-1], **x,
                     id=ln["lane"], name=name, family=name, given="", cls="", team="", relay=None, lane=ln["lane"], device_id=ln["device_id"],
                     pending=sum(1 for f in flags.values() if f["device_id"] == ln["device_id"] and not f["decision"]), fired=bool(shots))
            if with_shots:
                e["shot_list"] = shots
            rows.append(e)
        ranked = ranking.rank(rows, rules, decimal)
        for e in ranked:
            e.pop("summary", None)
        return {"id": mid, "name": m["name"], "format_name": fmt["name"], "scoring": fmt.get("scoring"), "tie_rules": rules,
                "record_shots": mf.record_shots(fmt), "team_size": 0, "best_count": False,
                "relays": [{"match_id": mid, "relay": None, "state": m["state"], "clock": self.matches._clock(m, self.core.now_ms())}],
                "classes": [{"name": "", "individuals": ranked, "teams": []}]}

    def listing(self, hidden_too: bool = False) -> List[dict]:
        """Every result, newest first: competitions, single matches that have started, frozen copies. hidden_too (Match
        Control): also those whose results were deleted, marked hidden."""
        c = self.db.conn()
        out = []
        for comp in self.competitions.list():
            if comp["results_hidden"] and not hidden_too:
                continue
            out.append({"key": f"c{comp['id']}", "kind": "competition", "name": comp["name"], "format_name": comp["format_name"],
                        "active": comp["active"], "hidden": bool(comp["results_hidden"]), "at_ms": comp["created_ms"]})
        for m in c.execute("SELECT * FROM matches WHERE competition_id IS NULL AND state!='ready' ORDER BY id DESC LIMIT 200"):
            if m["results_hidden"] and not hidden_too:
                continue
            out.append({"key": f"m{m['id']}", "kind": "match", "name": m["name"], "format_name": json.loads(m["format_json"])["name"],
                        "active": m["state"] in LIVE or m["state"] == "overtime", "hidden": bool(m["results_hidden"]), "at_ms": m["created_ms"]})
        for r in c.execute("SELECT id, kind, name, json, created_ms FROM results_archive ORDER BY id DESC LIMIT 500"):
            res = json.loads(r["json"])
            out.append({"key": f"a{r['id']}", "kind": "frozen", "name": r["name"], "format_name": res["format_name"],
                        "active": False, "hidden": False, "at_ms": res.get("at_ms", r["created_ms"])})
        return sorted(out, key=lambda x: -x["at_ms"])

    # ------------------------------------------------------------------------------------------- deleting
    def delete_results(self, keys: List, by: str) -> int:
        """Take results off the Results page (a frozen copy: delete it). All or nothing."""
        parsed = self._keys(keys)
        with self.db.write() as c:
            for kind, n in parsed:
                table = {"c": "competitions", "m": "matches", "a": "results_archive"}[kind]
                if c.execute(f"SELECT 1 FROM {table} WHERE id=?", (n,)).fetchone() is None:
                    raise MatchError(f"no such results: {kind}{n}")
            for kind, n in parsed:
                if kind == "a":
                    c.execute("DELETE FROM results_archive WHERE id=?", (n,))
                else:
                    c.execute(f"UPDATE {'competitions' if kind == 'c' else 'matches'} SET results_hidden=1 WHERE id=?", (n,))
        self.core.hub.publish({"type": "results"})
        return len(parsed)

    def restore_results(self, keys: List, by: str) -> int:
        """Show deleted results of a match or competition that still exists again."""
        parsed = self._keys(keys)
        if any(k == "a" for k, _ in parsed):
            raise MatchError("a deleted frozen copy cannot be brought back")
        with self.db.write() as c:
            for kind, n in parsed:
                c.execute(f"UPDATE {'competitions' if kind == 'c' else 'matches'} SET results_hidden=0 WHERE id=?", (n,))
        self.core.hub.publish({"type": "results"})
        return len(parsed)

    def delete_matches(self, match_ids: List, competition_ids: List, by: str) -> Dict[str, int]:
        """Delete single matches and competitions (with every relay) from Match Control, keeping a frozen copy of
        their results. All or nothing: nothing is deleted if any of them is running or unknown."""
        mids, cids = self._ids(match_ids), self._ids(competition_ids)
        if not mids and not cids:
            raise MatchError("choose what to delete")
        if len(mids) + len(cids) > MAX_DELETE:
            raise MatchError(f"at most {MAX_DELETE} at once")
        now = self.core.now_ms()
        frozen = 0
        with self.db.write() as c:
            todo = []                                       # (kind, id, name, results_hidden, [match rows])
            for mid in mids:
                m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
                if m is None:
                    raise MatchError(f"no such match: {mid}")
                if m["competition_id"] is not None:
                    raise MatchError(f"'{m['name']}' is a relay: delete its competition")
                todo.append(("match", mid, m["name"], m["results_hidden"], [m], m["created_ms"]))
            for cid in cids:
                comp = c.execute("SELECT * FROM competitions WHERE id=?", (cid,)).fetchone()
                if comp is None:
                    raise MatchError(f"no such competition: {cid}")
                todo.append(("competition", cid, comp["name"], comp["results_hidden"],
                             c.execute("SELECT * FROM matches WHERE competition_id=?", (cid,)).fetchall(), comp["created_ms"]))
            for kind, n, name, _, rows, _ in todo:
                busy = [m["name"] for m in rows if m["state"] in LIVE or m["state"] == "overtime"]
                if busy:
                    raise MatchError(f"'{busy[0]}' is running: stop it before deleting")
            for kind, n, name, hidden, rows, made in todo:
                started = any(m["state"] != "ready" for m in rows)
                if started and not hidden:                 # the results stay, frozen, on the Results page
                    res = self.competitions.results(n, with_shots=True) if kind == "competition" else self.match_results(n, with_shots=True)
                    res["at_ms"] = made                    # listed under the match's date, not the day it was deleted
                    c.execute("INSERT INTO results_archive (kind, source_id, name, json, created_ms, created_by) VALUES (?,?,?,?,?,?)",
                              (kind, n, name, json.dumps(res), now, by))
                    frozen += 1
                for m in rows:
                    for table in ("match_lanes", "match_log", "shot_decisions"):
                        c.execute(f"DELETE FROM {table} WHERE match_id=?", (m["id"],))
                    c.execute("DELETE FROM matches WHERE id=?", (m["id"],))
                if kind == "competition":
                    c.execute("DELETE FROM competitors WHERE competition_id=?", (n,))
                    c.execute("DELETE FROM competitions WHERE id=?", (n,))
        for kind, n, *_ in todo:
            self.core.hub.publish({"type": "competition" if kind == "competition" else "match",
                                   ("competition_id" if kind == "competition" else "match_id"): n})
        return {"deleted": len(todo), "frozen": frozen}

    @staticmethod
    def _ids(v) -> List[int]:
        if v is None:
            return []
        if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            raise MatchError("ids must be a list of numbers")
        return list(dict.fromkeys(v))

    @staticmethod
    def _keys(keys) -> List[tuple]:
        if not isinstance(keys, list) or not keys:
            raise MatchError("choose the results")
        if len(keys) > MAX_DELETE:
            raise MatchError(f"at most {MAX_DELETE} at once")
        return list(dict.fromkeys(parse_key(k) for k in keys))
