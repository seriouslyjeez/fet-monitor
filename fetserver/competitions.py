"""Competitions: one format, a list of competitors (family / given name, optional class and team) and one or more
relays. Each relay is a match (matches.py: its own clock and lanes); results and teams combine every relay that was
not aborted, ranked per class with the format's tie-break rules (ranking.py).

Teams: competitors with the same team name (within a class). The team size comes from the format's rulebook (ISSF 3,
6.11.10; CMP 4, 3.2; NRA set by the program, 4 suggested) and can be changed. With best_count on (CMP 3.2.2 c,
"Best-Four-Count Teams"), a team may have more members and its best `team_size` scores count; otherwise a team must
have exactly team_size members. Teams that do not qualify are listed, unranked, with the reason.
"""
from __future__ import annotations

import json
from functools import cmp_to_key
from typing import Dict, List, Optional

from . import matchformats as mf
from . import ranking
from .matches import ACTIVE, PACING, MatchError, own_clock, rules_of

MAX_COMPETITORS = 500


def display_name(r) -> str:
    """Results style: FAMILY Given."""
    return (r["family"].upper() + (" " + r["given"] if r["given"] else "")).strip()


class Competitions:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.matches = core.matches

    def _pub(self, cid):
        self.core.hub.publish({"type": "competition", "competition_id": cid})

    def _row(self, c, cid):
        r = c.execute("SELECT * FROM competitions WHERE id=?", (cid,)).fetchone()
        if r is None:
            raise MatchError("unknown competition")
        return r

    # ------------------------------------------------------------------------------------------- settings
    def create(self, name, fmt_id, by: str, override: Optional[dict] = None, team_size=None, best_count=False, paced="spoken") -> int:
        if not isinstance(name, str) or not name.strip():
            raise MatchError("the competition needs a name")
        base = self.matches.format(fmt_id) if isinstance(fmt_id, str) else None
        if base is None:
            raise MatchError("unknown format")
        fmt = base
        if override is not None:
            try:
                fmt = mf.validate(dict(override, id=base["id"], rulebook=base["rulebook"], edition=base.get("edition", ""),
                                       tie_rules=rules_of(base)))
            except ValueError as exc:
                raise MatchError(str(exc))
            if [(p["kind"], p["seconds"], p["shots"]) for p in fmt["phases"]] != [(p["kind"], p["seconds"], p["shots"]) for p in base["phases"]]:
                fmt["modified"] = True
        size = fmt.get("team_size", 4) if team_size is None else team_size
        self._check_team(size, best_count)
        if paced not in PACING:
            raise MatchError("commands: spoken, officer or auto")
        with self.db.write() as c:
            cid = c.execute("INSERT INTO competitions (name, format_json, team_size, best_count, paced, created_ms, created_by) "
                            "VALUES (?,?,?,?,?,?,?)", (name.strip()[:80], json.dumps(fmt), size, int(bool(best_count)), paced,
                                                       self.core.now_ms(), by)).lastrowid
        self._pub(cid)
        return cid

    @staticmethod
    def _check_team(size, best_count):
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 10:
            raise MatchError("team size must be 1 to 10")
        if not isinstance(best_count, bool):
            raise MatchError("best count must be true or false")

    def update(self, cid: int, name=None, team_size=None, best_count=None, paced=None, challenge_min=None) -> None:
        with self.db.write() as c:
            r = self._row(c, cid)
            size = r["team_size"] if team_size is None else team_size
            best = bool(r["best_count"]) if best_count is None else best_count
            self._check_team(size, best)
            if paced is not None and paced not in PACING:
                raise MatchError("commands: spoken, officer or auto")
            nm = r["name"] if name is None else (name.strip()[:80] if isinstance(name, str) and name.strip() else None)
            if not nm:
                raise MatchError("the competition needs a name")
            if challenge_min is not None and (not isinstance(challenge_min, int) or isinstance(challenge_min, bool) or not 1 <= challenge_min <= 1440):
                raise MatchError("challenge time: 1 to 1440 minutes")
            c.execute("UPDATE competitions SET name=?, team_size=?, best_count=?, paced=?, challenge_min=? WHERE id=?",
                      (nm, size, int(best), paced or r["paced"], r["challenge_min"] if challenge_min is None else challenge_min, cid))
        self._pub(cid)

    # ------------------------------------------------------------------------------------------- competitors
    @staticmethod
    def _clean(v, n=60):
        return v.strip()[:n] if isinstance(v, str) and v.strip() else None

    def save_competitor(self, cid: int, data: dict, competitor_id: Optional[int] = None) -> int:
        family = self._clean(data.get("family"))
        if not family:
            raise MatchError("a competitor needs a family name")
        vals = (family, self._clean(data.get("given")), self._clean(data.get("class"), 40), self._clean(data.get("team")))
        with self.db.write() as c:
            self._row(c, cid)
            if competitor_id is None:
                if c.execute("SELECT COUNT(*) FROM competitors WHERE competition_id=?", (cid,)).fetchone()[0] >= MAX_COMPETITORS:
                    raise MatchError(f"at most {MAX_COMPETITORS} competitors")
                competitor_id = c.execute("INSERT INTO competitors (competition_id, family, given, class, team, created_ms) VALUES (?,?,?,?,?,?)",
                                          (cid, *vals, self.core.now_ms())).lastrowid
            else:
                if c.execute("UPDATE competitors SET family=?, given=?, class=?, team=? WHERE id=? AND competition_id=?",
                             (*vals, competitor_id, cid)).rowcount != 1:
                    raise MatchError("unknown competitor")
                name = display_name({"family": vals[0], "given": vals[1]})
                # the new name everywhere it shows: the relay lanes, and the shooting session the target screens show
                for ln in c.execute("SELECT series_id FROM match_lanes WHERE competitor_id=?", (competitor_id,)).fetchall():
                    if ln["series_id"] is not None:
                        c.execute("UPDATE series SET shooter=? WHERE id=?", (name, ln["series_id"]))
                        c.execute("UPDATE segments SET shooter=? WHERE series_id=?", (name, ln["series_id"]))
                c.execute("UPDATE match_lanes SET competitor=? WHERE competitor_id=?", (name, competitor_id))
        self._pub(cid)
        return competitor_id

    HEADERS = {"family": ("family", "family name", "last", "last name", "lastname", "surname", "name"),
               "given": ("given", "given name", "first", "first name", "firstname", "forename"),
               "class": ("class", "category", "division"), "team": ("team", "club", "team name")}

    def import_csv(self, cid: int, text: str) -> dict:
        """Competitors from CSV text (processed in memory; nothing is stored but the competitors). Columns: Family,
        Given, Class, Team, or any order with a header row naming them. Comma, semicolon or tab separated. All or
        nothing: if any line has a problem nothing is added, and every problem is reported with its line number."""
        import csv
        import io
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            raise MatchError("the file has no competitors")
        sample = "\n".join(lines[:20])
        delim = max((",", ";", "\t"), key=lambda d: sample.count(d)) if any(d in sample for d in (",", ";", "\t")) else ","
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
        cols = {"family": 0, "given": 1, "class": 2, "team": 3}
        first = [x.strip().casefold() for x in rows[0]]
        start = 1
        named = {k: next((i for i, h in enumerate(first) if h in names), None) for k, names in self.HEADERS.items()}
        if named["family"] is not None and any(h in sum(self.HEADERS.values(), ()) for h in first):
            cols = {k: v for k, v in named.items()}
        else:
            start = 0                                     # no header: Family, Given, Class, Team
        people, problems = [], []
        for n, row in enumerate(rows[start:], start + 1):
            get = lambda k: (row[cols[k]].strip() if cols[k] is not None and cols[k] < len(row) else "")
            rec = {k: get(k) for k in cols}
            if not rec["family"]:
                problems.append(f"line {n}: no family name")
            elif len(rec["family"]) > 60 or len(rec["given"]) > 60 or len(rec["class"]) > 40 or len(rec["team"]) > 60:
                problems.append(f"line {n}: a field is too long")
            else:
                people.append(rec)
        if problems:
            raise MatchError("nothing was added. " + "; ".join(problems[:20]) + (" …" if len(problems) > 20 else ""))
        with self.db.write() as c:
            self._row(c, cid)
            have = c.execute("SELECT COUNT(*) FROM competitors WHERE competition_id=?", (cid,)).fetchone()[0]
            if have + len(people) > MAX_COMPETITORS:
                raise MatchError(f"at most {MAX_COMPETITORS} competitors (this file would make {have + len(people)})")
            now = self.core.now_ms()
            for p in people:
                c.execute("INSERT INTO competitors (competition_id, family, given, class, team, created_ms) VALUES (?,?,?,?,?,?)",
                          (cid, p["family"], p["given"] or None, p["class"] or None, p["team"] or None, now))
        self._pub(cid)
        return {"added": len(people)}

    def remove_competitor(self, cid: int, competitor_id: int) -> None:
        with self.db.write() as c:
            used = c.execute("""SELECT m.state FROM match_lanes l JOIN matches m ON m.id=l.match_id
                                WHERE l.competitor_id=? AND m.state!='aborted'""", (competitor_id,)).fetchone()
            if used:
                raise MatchError("this competitor is on a relay: take them off the relay first (or stop that relay)")
            if c.execute("DELETE FROM competitors WHERE id=? AND competition_id=?", (competitor_id, cid)).rowcount != 1:
                raise MatchError("unknown competitor")
        self._pub(cid)

    # ------------------------------------------------------------------------------------------- relays
    def add_relay(self, cid: int, lanes: List[dict], by: str) -> int:
        """A relay: [{"device_id", "competitor_id"}] in firing-point order. Each competitor shoots in one relay (a relay
        that was stopped frees its competitors)."""
        c = self.db.conn()
        comp = self._row(c, cid)
        if not isinstance(lanes, list) or not lanes:
            raise MatchError("a relay needs at least one target")
        competitors = {r["id"]: r for r in c.execute("SELECT * FROM competitors WHERE competition_id=?", (cid,))}
        taken = {r["competitor_id"] for r in c.execute("""SELECT l.competitor_id FROM match_lanes l JOIN matches m ON m.id=l.match_id
                                                          WHERE m.competition_id=? AND m.state!='aborted' AND l.competitor_id IS NOT NULL AND m.final_kind IS NULL""", (cid,))}
        out, seen = [], set()
        for ln in lanes:
            pid = ln.get("competitor_id") if isinstance(ln, dict) else None
            if pid is None:
                raise MatchError("every firing point needs a competitor")
            if pid not in competitors:
                raise MatchError("unknown competitor")
            if pid in taken or pid in seen:
                raise MatchError(f"{display_name(competitors[pid])} is already on a relay")
            seen.add(pid)
            out.append({"device_id": ln.get("device_id"), "competitor": display_name(competitors[pid]), "competitor_id": pid})
        relay = (c.execute("SELECT MAX(relay) FROM matches WHERE competition_id=?", (cid,)).fetchone()[0] or 0) + 1
        fmt = json.loads(comp["format_json"])
        return self.matches.create(f"{comp['name']} - relay {relay}", None, out, by, paced=comp["paced"],
                                   fmt=fmt, competition_id=cid, relay=relay)

    def move_competitor(self, cid: int, competitor_id: int, match_id, device_id, by: str) -> None:
        """Before a relay starts: put a competitor on a relay and target (match_id, device_id), or take them off their
        relay (match_id None). Moving onto a target where someone else is swaps the two. During a match a shooter is
        moved from the relay's console instead (their shots and time go with them: Matches.move)."""
        with self.db.write() as c:
            self._row(c, cid)
            person = c.execute("SELECT * FROM competitors WHERE id=? AND competition_id=?", (competitor_id, cid)).fetchone()
            if person is None:
                raise MatchError("unknown competitor")
            cur = c.execute("""SELECT l.*, m.state, m.relay FROM match_lanes l JOIN matches m ON m.id=l.match_id
                               WHERE l.competitor_id=? AND m.competition_id=? AND m.state!='aborted' AND l.left_ms IS NULL AND m.final_kind IS NULL""", (competitor_id, cid)).fetchone()
            if cur is not None and cur["state"] != "ready":
                raise MatchError(f"relay {cur['relay']} has started: move this shooter on its console (Move)")
            if match_id is None:
                if cur is None:
                    raise MatchError("this competitor is not on a relay")
                self._leave(c, cur)
                self._pub(cid)
                return
            m = c.execute("SELECT * FROM matches WHERE id=? AND competition_id=?", (match_id, cid)).fetchone() if isinstance(match_id, int) else None
            if m is None:
                raise MatchError("unknown relay")
            if m["state"] != "ready":
                raise MatchError(f"relay {m['relay']} has started: competitors can be moved onto it only before the start")
            if not isinstance(device_id, str):
                raise MatchError("choose a target")
            if cur is not None and cur["match_id"] == match_id and cur["device_id"] == device_id:
                return
            name = display_name(person)
            there = c.execute("SELECT * FROM match_lanes WHERE match_id=? AND device_id=?", (match_id, device_id)).fetchone()
            if there is not None:
                if cur is None:
                    raise MatchError(f"{there['competitor'] or 'someone'} is on that target: move them first")
                for slot, who in ((there, (name, competitor_id)), (cur, (there["competitor"], there["competitor_id"]))):   # swap
                    c.execute("UPDATE match_lanes SET competitor=?, competitor_id=? WHERE match_id=? AND device_id=?",
                              (*who, slot["match_id"], slot["device_id"]))
            elif cur is not None and cur["match_id"] == match_id:          # to a free target in the same relay
                self._check_free(c, device_id)
                c.execute("UPDATE match_lanes SET device_id=? WHERE match_id=? AND device_id=?", (device_id, match_id, cur["device_id"]))
            else:
                self._check_free(c, device_id)
                if cur is not None:
                    self._leave(c, cur)
                lane = (c.execute("SELECT MAX(lane) FROM match_lanes WHERE match_id=?", (match_id,)).fetchone()[0] or 0) + 1
                c.execute("INSERT INTO match_lanes (match_id, device_id, lane, competitor, competitor_id) VALUES (?,?,?,?,?)",
                          (match_id, device_id, lane, name, competitor_id))
            self.matches._log(c, match_id, self.core.now_ms(), "seat", None, device_id, {"competitor_id": competitor_id, "by": by})
        self._pub(cid)
        self.matches._pub(match_id)

    def finish_on_later_relay(self, mid: int, device_id: str, to_match, to_device: str, by: str) -> int:
        """CMP 7.18: a shooter whose target failed finishes the match on a later relay. Their shots so far stay on this
        relay; on the later one (to_match: a relay that has not started, or None for a new relay) they wait until the
        phase they left, then get the time they had left there plus 2 minutes (7.18.3), sighters first, and only the
        record shots still to fire. Returns the later relay's match id."""
        now = self.core.now_ms()
        with self.db.write() as c:
            m = self.matches._row(c, mid)
            if m["competition_id"] is None:
                raise MatchError("only a competition's relay can send a shooter to a later relay")
            if m["state"] not in ("running", "paused", "holding", "overtime"):
                raise MatchError("the relay is not running")
            self.matches.not_official(m)
            ln = self.matches._lane(c, mid, device_id)
            if ln["left_ms"] is not None or ln["competitor_id"] is None:
                raise MatchError("this firing point has no competitor to move")
            fmt = json.loads(m["format_json"])
            phase = ln["own_phase"] if ln["own_phase"] is not None else m["phase"]
            if phase < 0 or fmt["phases"][phase]["kind"] not in mf.FIRE_KINDS:
                raise MatchError("between stages: place the competitor on a later relay with the competitor window instead")
            left = self.matches.lane_left_ms(m, ln, now)
            wins = [w for w in self.matches.windows(c, mid) if w["phase"] == phase]
            start = max((w["start"] for w in wins if w["attempt"] == max(x["attempt"] for x in wins)), default=0) if wins else 0
            fired = c.execute("SELECT COUNT(*) FROM shots WHERE series_id=? AND stream='scored' AND event_ms>=?", (ln["series_id"], start)).fetchone()[0] \
                if ln["series_id"] is not None else 0
            comp = self._row(c, m["competition_id"])
            if to_match is None:
                self._check_free(c, to_device)
                relay = (c.execute("SELECT MAX(relay) FROM matches WHERE competition_id=?", (comp["id"],)).fetchone()[0] or 0) + 1
                to = None
            else:
                t = c.execute("SELECT * FROM matches WHERE id=? AND competition_id=?", (to_match, comp["id"])).fetchone() if isinstance(to_match, int) else None
                if t is None or t["state"] != "ready" or t["relay"] <= m["relay"]:
                    raise MatchError("choose a later relay that has not started")
                if c.execute("SELECT 1 FROM match_lanes WHERE match_id=? AND device_id=?", (to_match, to_device)).fetchone():
                    raise MatchError("that target is taken on that relay")
                self._check_free(c, to_device)
                to = to_match
        if to is None:                                   # a new relay (its own transaction, like any relay)
            to = self.matches.create(f"{comp['name']} - relay {relay}", None, [{"device_id": to_device, "competitor": ln["competitor"],
                                                                                   "competitor_id": ln["competitor_id"]}], by,
                                     paced=comp["paced"], fmt=fmt, competition_id=comp["id"], relay=relay)
        with self.db.write() as c:
            if c.execute("SELECT 1 FROM match_lanes WHERE match_id=? AND device_id=?", (to, to_device)).fetchone() is None:
                lane = (c.execute("SELECT MAX(lane) FROM match_lanes WHERE match_id=?", (to,)).fetchone()[0] or 0) + 1
                c.execute("INSERT INTO match_lanes (match_id, device_id, lane, competitor, competitor_id) VALUES (?,?,?,?,?)",
                          (to, to_device, lane, ln["competitor"], ln["competitor_id"]))
            c.execute("UPDATE match_lanes SET start_phase=?, carry_shots=?, carry_ms=?, from_match=? WHERE match_id=? AND device_id=?",
                      (phase, fired, left, mid, to, to_device))
            c.execute("UPDATE match_lanes SET left_ms=?, to_match=?, moving_since_ms=NULL, moving_left_ms=NULL, extra_ms=0, own_phase=NULL, "
                      "own_left_ms=NULL, own_since_ms=NULL, sighting=0 WHERE match_id=? AND device_id=?", (now, to, mid, device_id))
            seg = c.execute("SELECT id FROM segments WHERE device_id=? AND ended_ms IS NULL", (device_id,)).fetchone()
            if seg is not None:                          # the failed target leaves the match (its shots so far stay)
                c.execute("UPDATE segments SET ended_ms=? WHERE id=?", (now, seg["id"]))
            if ln["series_id"] is not None:
                c.execute("UPDATE series SET ended_ms=? WHERE id=? AND ended_ms IS NULL", (now, ln["series_id"]))
            self.matches._log(c, mid, now, "left_relay", phase, device_id, {"to_match": to, "to_device": to_device, "shots": fired, "left_ms": left, "by": by})
            self.matches._log(c, to, now, "joins_relay", phase, to_device, {"from_match": mid, "shots": fired, "left_ms": left, "by": by})
            mm = self.matches._row(c, mid)
            if mm["state"] == "overtime" and not any(own_clock(x) for x in self.matches._lanes(c, mid) if x["left_ms"] is None):
                self.matches._done(c, mm, now)
        self.matches._pub(mid)
        self.matches._pub(to)
        self._pub(comp["id"])
        return to

    def add_late(self, mid: int, competitor_id, device_id: str, by: str) -> None:
        """A competitor who is on no relay arrives late: they join this running relay (ISSF 6.11.4: no extra time)."""
        c = self.db.conn()
        m = self.matches._row(c, mid)
        p = c.execute("SELECT * FROM competitors WHERE id=? AND competition_id=?", (competitor_id, m["competition_id"])).fetchone() \
            if isinstance(competitor_id, int) and m["competition_id"] is not None else None
        if p is None:
            raise MatchError("unknown competitor")
        if c.execute("""SELECT 1 FROM match_lanes l JOIN matches x ON x.id=l.match_id WHERE l.competitor_id=? AND x.state!='aborted'
                        AND l.left_ms IS NULL AND x.final_kind IS NULL""", (competitor_id,)).fetchone():
            raise MatchError(f"{display_name(p)} is already on a relay")
        self.matches.add_late(mid, device_id, display_name(p), by, competitor_id)
        self._pub(m["competition_id"])

    # ------------------------------------------------------------------------------------------- finals (CMP 10)
    def create_final(self, cid: int, kind: str, devices: List[str], by: str, cls: Optional[str] = None, n: Optional[int] = None,
                     for_relay: Optional[int] = None, paced: str = "officer") -> int:
        """A final. Standard (CMP 10.4): the top n of the class (n up to 8, at most one per target) on the targets given,
        best qualifier on the first (point A, 10.4.1). for_relay: "Final for All Athletes" (10.1.2 a), everyone of
        that finished relay on their own target. The final is a match of the competition; paced "officer" (default:
        the range officer gives LOAD for every shot) or "spoken" (the console speaks the commands, with a pause for the
        scores before each LOAD)."""
        if kind != "standard":
            raise MatchError("this final is not built yet")
        if paced not in ("officer", "spoken"):
            raise MatchError("a final's commands: officer (the range officer reads them) or spoken")
        c = self.db.conn()
        comp = self._row(c, cid)
        fmt = mf.cmp_standard_final(spoken=paced == "spoken")
        if for_relay is not None:
            r = c.execute("SELECT * FROM matches WHERE id=? AND competition_id=? AND final_kind IS NULL", (for_relay, cid)).fetchone() if isinstance(for_relay, int) else None
            if r is None or r["state"] != "done":
                raise MatchError("a relay's final comes after that relay has finished")
            if c.execute("SELECT 1 FROM matches WHERE final_for=? AND state!='aborted'", (for_relay,)).fetchone():
                raise MatchError("that relay has its final already")
            lanes = [{"device_id": l["device_id"], "competitor": l["competitor"], "competitor_id": l["competitor_id"]}
                     for l in c.execute("SELECT * FROM match_lanes WHERE match_id=? AND left_ms IS NULL AND competitor_id IS NOT NULL ORDER BY lane", (for_relay,))]
            name = f"{comp['name']} - relay {r['relay']} final"
        else:
            if not isinstance(devices, list) or not devices or len(set(devices)) != len(devices):
                raise MatchError("choose the targets for the final, in order (A, B, C ...)")
            want = min(8, len(devices)) if n is None else n
            if not isinstance(want, int) or isinstance(want, bool) or not 1 <= want <= min(8, len(devices)):
                raise MatchError("finalists: 1 to 8, at most one per target")
            res = self.results(cid)
            group = next((x for x in res["classes"] if x["name"] == (cls or "")), None)
            if group is None:
                raise MatchError("no such class")
            ranked = [e for e in group["individuals"] if e["fired"] and not e.get("dq")]
            if len(ranked) < want:
                raise MatchError(f"only {len(ranked)} competitors have a score")
            lanes = [{"device_id": d, "competitor": e["name"], "competitor_id": e["id"]} for d, e in zip(devices, ranked[:want])]
            name = f"{comp['name']} - final" + (f" ({cls})" if cls else "")
        mid = self.matches.create(name, None, lanes, by, paced=paced, fmt=fmt, competition_id=cid, relay=None)
        with self.db.write() as w:
            w.execute("UPDATE matches SET final_kind=?, final_for=?, final_class=? WHERE id=?", (kind, for_relay, cls or None, mid))
            for i, l in enumerate(w.execute("SELECT device_id FROM match_lanes WHERE match_id=? ORDER BY lane", (mid,)).fetchall()):
                w.execute("UPDATE match_lanes SET final_pos=? WHERE match_id=? AND device_id=?", (chr(65 + i), mid, l["device_id"]))
        self._pub(cid)
        return mid

    def _finals(self, c, comp, finals, classes, decimal: bool, with_shots: bool) -> List[dict]:
        """Each final's standings. Standard (CMP 10.4.3): qualification + final; ties for places 1-3 by shoot-off
        (10.2.9 n), the others by the final score, then the final's shots by CMP 8.6.1 e. A relay's "final for all"
        (10.1.2 a) lists everyone, but only the class's top 8 qualifiers count (counts=True); the "all" table ranks them."""
        qual = {e["id"]: e for cl in classes for e in cl["individuals"]}
        top8 = {e["id"] for cl in classes for e in cl["individuals"][:8] if e["fired"] and not e.get("dq")}
        out, pooled = [], []
        for m in finals:
            if m["state"] == "aborted":
                continue
            fmt = json.loads(m["format_json"])
            wins = self.matches.windows(c, m["id"])
            off_phases = {i for i, p in enumerate(fmt["phases"]) if p.get("lanes") is not None}
            flags = {f["shot_id"]: f for f in (self.matches.flags(c, m) if m["state"] != "ready" else [])}
            rows = []
            for ln in self.matches._lanes(c, m["id"]):
                shots, x = self.matches.counted_row(c, m, ln, flags)
                phase_of = lambda t: next((w["phase"] for w in wins if w["start"] <= t and (w["end"] is None or t < w["end"])), None)
                main = [s for s in shots if phase_of(s["t"]) not in off_phases]
                off = [s["tenths"] for s in shots if phase_of(s["t"]) in off_phases]
                q = qual.get(ln["competitor_id"], {})
                ft = sum(s["tenths"] if decimal else s["ring"] * 10 for s in main) / 10.0
                row = {"pos": ln["final_pos"], "lane": ln["lane"], "device_id": ln["device_id"], "id": ln["competitor_id"], "name": ln["competitor"] or "",
                       "family": q.get("family", ""), "cls": q.get("cls", ""), "qual": q.get("total"), "final": ft, "shots": len(main),
                       "total": round((q.get("total") or 0) + ft, 1), "shoot_off": [v / 10.0 for v in off], "dq": x["dq"],
                       "counts": m["final_for"] is None or ln["competitor_id"] in top8, "summary": ranking.summary(main)}
                if with_shots:
                    row["shot_list"] = main
                rows.append(row)
            entry = {"match_id": m["id"], "name": m["name"], "kind": m["final_kind"], "cls": m["final_class"] or "", "for_relay": m["final_for"],
                     "state": m["state"], "official_ms": m["official_ms"], "rows": self._rank_final(rows)}
            entry["shoot_off_needed"] = self._ties_for_medals(entry["rows"]) if m["state"] == "done" and m["final_kind"] == "standard" else []
            out.append(entry)
            if m["final_for"] is not None:
                pooled += [r for r in rows if r["counts"]]
        if pooled:                                       # Final for All Athletes: the top 8 of each class, ranked together
            for cls in sorted({r["cls"] for r in pooled}):
                out.append({"match_id": None, "name": "Final for all athletes" + (f" ({cls})" if cls else ""), "kind": "standard_all", "cls": cls,
                            "for_relay": None, "state": None, "official_ms": None, "shoot_off_needed": [],
                            "rows": self._rank_final([dict(r) for r in pooled if r["cls"] == cls], shoot_offs=False)})
        for f in out:
            for r in f["rows"]:
                r.pop("summary", None)
        return out

    @staticmethod
    def _rank_final(rows: List[dict], shoot_offs: bool = True) -> List[dict]:
        """Places by total (CMP 10.4.3). A tie that reaches into places 1-3 stays tied until a shoot-off breaks it
        (10.2.9 n); other ties: the final score, then the final's shots by CMP 8.6.1 e (8.6.2 b). Without shoot-offs
        (the pooled final for all athletes) every tie goes by the final score first (10.4.8 c)."""
        cmp = ranking._cmp
        live = sorted([r for r in rows if not r["dq"]], key=cmp_to_key(lambda a, b: cmp((b["total"], b["shoot_off"]), (a["total"], a["shoot_off"]))))
        out, i = [], 0
        while i < len(live):
            j = i
            while j + 1 < len(live) and cmp((live[j + 1]["total"], live[j + 1]["shoot_off"]), (live[i]["total"], live[i]["shoot_off"])) == 0:
                j += 1
            group, place = live[i:j + 1], i + 1
            if len(group) > 1 and (place > 3 or not shoot_offs):
                key = lambda r: (r["final"], ranking.sort_key(r["summary"], "CMP", True))
                group.sort(key=cmp_to_key(lambda a, b: cmp(key(b), key(a))))
                for k, r in enumerate(group):
                    same = k > 0 and cmp(key(group[k - 1]), key(r)) == 0
                    r["place"] = group[k - 1]["place"] if same else place + k
                for k, r in enumerate(group):
                    r["tied"] = any(x is not r and x["place"] == r["place"] for x in group)
            else:
                for r in group:
                    r["place"], r["tied"] = place, len(group) > 1
            out += group
            i = j + 1
        return out + [dict(r, place=None, tied=False) for r in rows if r["dq"]]

    @staticmethod
    def _ties_for_medals(rows: List[dict]) -> List[List[int]]:
        """Groups of firing points tied on total (and any shoot-off so far) for places 1-3 (CMP 10.2.9 n)."""
        groups: Dict[tuple, List[int]] = {}
        for r in rows:
            if r["place"] is not None and r["place"] <= 3:
                groups.setdefault((r["total"], tuple(r["shoot_off"])), []).append(r["lane"])
        return [sorted(v) for v in groups.values() if len(v) > 1]

    def _check_free(self, c, device_id: str) -> None:
        if c.execute("SELECT 1 FROM targets WHERE device_id=? AND removed=0 AND banned=0", (device_id,)).fetchone() is None:
            raise MatchError("unknown target")
        other = self.matches.lock_of(c, device_id)
        if other:
            raise MatchError(f"that target is in '{other}'")

    @staticmethod
    def _leave(c, lane) -> None:
        if c.execute("SELECT COUNT(*) FROM match_lanes WHERE match_id=?", (lane["match_id"],)).fetchone()[0] <= 1:
            raise MatchError(f"the last competitor of relay {lane['relay']} cannot leave it: stop that relay instead")
        c.execute("DELETE FROM match_lanes WHERE match_id=? AND device_id=?", (lane["match_id"], lane["device_id"]))

    # ------------------------------------------------------------------------------------------- reading
    def list(self) -> List[dict]:
        c = self.db.conn()
        out = []
        for r in c.execute("SELECT * FROM competitions ORDER BY id DESC LIMIT 100"):
            fmt = json.loads(r["format_json"])
            relays = [dict(x) for x in c.execute("SELECT id, relay, state FROM matches WHERE competition_id=? AND final_kind IS NULL ORDER BY relay", (r["id"],))]
            n = c.execute("SELECT COUNT(*) FROM competitors WHERE competition_id=?", (r["id"],)).fetchone()[0]
            out.append({"id": r["id"], "name": r["name"], "format_name": fmt["name"], "competitors": n, "relays": relays,
                        "active": any(x["state"] in ACTIVE for x in relays),
                        "busy": any(x["state"] in ACTIVE and x["state"] != "ready" for x in relays),
                        "results_hidden": bool(r["results_hidden"]), "created_ms": r["created_ms"]})
        return out

    def detail(self, cid: int) -> dict:
        c = self.db.conn()
        r = self._row(c, cid)
        fmt = json.loads(r["format_json"])
        lanes = {l["competitor_id"]: l for l in c.execute(
            """SELECT l.*, m.relay, m.state FROM match_lanes l JOIN matches m ON m.id=l.match_id
               WHERE m.competition_id=? AND m.state!='aborted' AND l.left_ms IS NULL AND m.final_kind IS NULL""", (cid,))}
        people = []
        for p in c.execute("SELECT * FROM competitors WHERE competition_id=? ORDER BY family COLLATE NOCASE, given COLLATE NOCASE", (cid,)):
            ln = lanes.get(p["id"])
            people.append({"id": p["id"], "family": p["family"], "given": p["given"], "class": p["class"], "team": p["team"],
                           "name": display_name(p), "relay": ln["relay"] if ln else None, "lane": ln["lane"] if ln else None,
                           "device_id": ln["device_id"] if ln else None})
        relays = [{"match_id": m["id"], "relay": m["relay"], "state": m["state"], "name": m["name"],
                   "lanes": [{"device_id": l["device_id"], "lane": l["lane"], "competitor": l["competitor"], "competitor_id": l["competitor_id"]}
                             for l in c.execute("SELECT * FROM match_lanes WHERE match_id=? ORDER BY lane", (m["id"],))]}
                  for m in c.execute("SELECT * FROM matches WHERE competition_id=? AND final_kind IS NULL ORDER BY relay", (cid,)).fetchall()]
        return {"id": cid, "name": r["name"], "format": fmt, "team_size": r["team_size"], "best_count": bool(r["best_count"]),
                "challenge_min": r["challenge_min"] if r["challenge_min"] is not None else 30,
                "paced": r["paced"], "tie_rules": rules_of(fmt), "record_shots": mf.record_shots(fmt),
                "competitors": people, "relays": relays,
                "finals": [{"match_id": m["id"], "kind": m["final_kind"], "cls": m["final_class"], "for_relay": m["final_for"], "state": m["state"], "name": m["name"]}
                           for m in c.execute("SELECT * FROM matches WHERE competition_id=? AND final_kind IS NOT NULL ORDER BY id", (cid,))]}

    def results(self, cid: int, with_shots: bool = False) -> dict:
        """Individuals and teams per class, ranked with the format's tie-break rules, from every relay not aborted."""
        c = self.db.conn()
        comp = self._row(c, cid)
        fmt = json.loads(comp["format_json"])
        decimal, rules = fmt.get("scoring") == "decimal", rules_of(fmt)
        people = {p["id"]: p for p in c.execute("SELECT * FROM competitors WHERE competition_id=?", (cid,))}
        found: Dict[int, dict] = {}
        relays = []
        finals = []
        for m in c.execute("SELECT * FROM matches WHERE competition_id=? ORDER BY relay", (cid,)).fetchall():
            if m["final_kind"]:                          # a final never counts in the qualification
                finals.append(m)
                continue
            relays.append({"match_id": m["id"], "relay": m["relay"], "state": m["state"], "clock": self.matches._clock(m, self.core.now_ms())})
            if m["state"] == "aborted":
                continue
            flags = {f["shot_id"]: f for f in (self.matches.flags(c, m) if m["state"] != "ready" else [])}
            for ln in c.execute("SELECT * FROM match_lanes WHERE match_id=?", (m["id"],)).fetchall():
                if ln["competitor_id"] in people:
                    shots, x = self.matches.counted_row(c, m, ln, flags)
                    part = {"shots": shots, "x": x, "relay": m["relay"], "lane": ln["lane"], "device_id": ln["device_id"],
                            "pending": sum(1 for f in flags.values() if f["device_id"] == ln["device_id"] and not f["decision"])}
                    prev = found.get(ln["competitor_id"])
                    if prev is None:
                        found[ln["competitor_id"]] = part
                    else:                                # finished on a later relay (CMP 7.18): both parts, in firing order
                        first, later = (prev, part) if ln["from_match"] is not None else (part, prev)
                        found[ln["competitor_id"]] = {"shots": first["shots"] + later["shots"], "relay": later["relay"], "lane": later["lane"],
                                                      "device_id": later["device_id"], "pending": first["pending"] + later["pending"],
                                                      "moved_from": {"relay": first["relay"], "lane": first["lane"]},
                                                      "x": {"dq": later["x"]["dq"] or first["x"]["dq"], "penalties": first["x"]["penalties"] + later["x"]["penalties"],
                                                            "removed": first["x"]["removed"] + later["x"]["removed"]}}
        classes: Dict[str, List[dict]] = {}
        for pid, p in people.items():
            f = found.get(pid, {"shots": [], "x": {"dq": None, "penalties": [], "removed": []}, "relay": None, "lane": None, "device_id": None, "pending": 0})
            if f.get("moved_from"):
                f["x"] = dict(f["x"], moved_from=f["moved_from"])
            e = dict(self.matches.entry_view(f["shots"], decimal), summary=ranking.summary(f["shots"]), sort_name=p["family"], **f["x"],
                     id=pid, name=display_name(p), family=p["family"], given=p["given"], cls=p["class"] or "", team=p["team"] or "",
                     relay=f["relay"], lane=f["lane"], device_id=f["device_id"], pending=f["pending"], fired=bool(f["shots"]))
            if with_shots:
                e["shot_list"] = f["shots"]
            classes.setdefault(e["cls"], []).append(e)
        out_classes = []
        for cls in sorted(classes, key=lambda k: (k == "", k.casefold())):
            ranked = ranking.rank(classes[cls], rules, decimal)
            teams = self._teams(ranked, comp, rules, decimal)
            for e in ranked:
                e.pop("summary", None)
            out_classes.append({"name": cls, "individuals": ranked, "teams": teams})
        return {"id": cid, "name": comp["name"], "format_name": fmt["name"], "scoring": fmt.get("scoring"), "tie_rules": rules,
                "record_shots": mf.record_shots(fmt), "team_size": comp["team_size"], "best_count": bool(comp["best_count"]),
                "relays": relays, "classes": out_classes, "finals": self._finals(c, comp, finals, out_classes, decimal, with_shots),
                "official_ms": self._official(c, cid), **self._posted(c, cid)}

    @staticmethod
    def _official(c, cid):
        """When the competition's results became official: every relay that was not stopped is official (the latest)."""
        rows = [r for r in c.execute("SELECT state, official_ms FROM matches WHERE competition_id=? AND state!='aborted'", (cid,))]
        return max(r["official_ms"] for r in rows) if rows and all(r["official_ms"] for r in rows) else None

    @staticmethod
    def _posted(c, cid) -> dict:
        """Preliminary results: posted when every relay that was not stopped is; protests until the latest close."""
        rows = [r for r in c.execute("SELECT results_posted_ms, protest_until_ms FROM matches WHERE competition_id=? AND state!='aborted'", (cid,))]
        done = bool(rows) and all(r["results_posted_ms"] for r in rows)
        return {"results_posted_ms": max(r["results_posted_ms"] for r in rows) if done else None,
                "protest_until_ms": max(r["protest_until_ms"] for r in rows) if done else None}

    @staticmethod
    def _teams(ranked: List[dict], comp, rules: str, decimal: bool) -> List[dict]:
        size, best = comp["team_size"], bool(comp["best_count"])
        groups: Dict[str, List[dict]] = {}
        for e in ranked:                                   # already in individual ranking order: best members first
            if e["team"]:
                groups.setdefault(e["team"], []).append(e)
        qualified, others = [], []
        for name, members in groups.items():
            if len(members) < size or (len(members) > size and not best):
                why = f"{len(members)} of {size} members" if len(members) < size else f"{len(members)} members for a team of {size}"
                others.append({"name": name, "members": [m["name"] for m in members], "counting": [], "reason": why})
                continue
            if rules == "ISSF" and any(m.get("dq") for m in members):     # ISSF 6.12.6.2 g; CMP/NRA say nothing: the member counts 0
                others.append({"name": name, "members": [m["name"] for m in members], "counting": [], "reason": "DSQ: a member was disqualified (ISSF 6.12.6.2 g)"})
                continue
            counting = members[:size]
            summ = ranking.combine([m["summary"] for m in counting])
            qualified.append({"name": name, "summary": summ, "sort_name": name, "members": [m["name"] for m in members],
                              "counting": [m["name"] for m in counting],
                              "total": (summ["total_tenths"] if decimal else summ["total_rings"] * 10) / 10.0, "x": summ["inners"]})
        ranked_teams = ranking.rank(qualified, rules, decimal) if qualified else []
        for t in ranked_teams:
            t.pop("summary", None)
        return ranked_teams + [dict(t, place=None, total=None) for t in sorted(others, key=lambda t: t["name"].casefold())]
