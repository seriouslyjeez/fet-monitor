"""Hosted virtual matches (deepTENS P4 stage 5; docs/PLAN-virtual-matches.md "Stage 5 design", docs/PROTOCOL.md
"Hosted virtual matches"). A deepTENS virtual match this range hosts is run here as a match in Match Control, with
its format as deepTENS copied it: the Pi keeps the clock (so an internet outage does not stop it) and carries its
targets' signed lines to deepTENS, which scores them and is the authority (this Pi's scores are provisional).

- Lanes: the Match Director puts entrants on targets (place), or an entrant signs in at a free lane (signed_in).
- A synchronized match starts by itself at deepTENS's start time; a late entrant joins within its late joining. A
  window match runs as relays the Match Director starts (a run must end before the window closes).
- Its clock is the match's: no pause, extra time, restart, move or abort (version 1); discharging air is allowed.
- Supervision: a run is range-supervised when staff (a Range Officer or the Match Director) had the match open in
  Match Control throughout it (gaps under CONSOLE_GAP_MS ignored); the gaps go to deepTENS.
- Each lane's run goes to deepTENS as an outbox item (kind hosted_run, key h<match id * 1000 + lane>)."""
from __future__ import annotations

import copy
import json
from typing import List, Optional

from fetcore import matchformats as mf

from .matches import MatchError

CONSOLE_GAP_MS = 60_000           # staff away from the console longer than this: the run is not supervised meanwhile
JOIN_BEFORE_MS = 30 * 60_000      # a synchronized match: lanes from this long before its start
REFUSED_ACTIONS = {"pause", "resume", "extra", "interrupt", "interrupt_end", "pacing", "abort", "go", "next"}
LINE_TYPES = ("target_shot", "target_hello")
LINE_EARLY_MS = 60_000            # lines this long before a run's start go too (a hello, a shot fired a moment early)


def run_ms(fmt: dict) -> int:
    return mf.total_seconds(fmt) * 1000


class Hosted:
    def __init__(self, core):
        self.core, self.db = core, core.db
        self.by_code = {}                              # code -> the hosted match as deepTENS sent it

    # ------------------------------------------------------------------------------------------------ deepTENS's list
    def set_matches(self, matches: list) -> None:
        self.by_code = {m["code"]: m for m in matches if isinstance(m, dict) and isinstance(m.get("code"), str)}

    def is_hosted(self, mid: int) -> bool:
        r = self.db.conn().execute("SELECT hosted_code FROM matches WHERE id=?", (mid,)).fetchone()
        return bool(r and r["hosted_code"])

    def local(self, c, code: str) -> list:
        return c.execute("SELECT * FROM matches WHERE hosted_code=? ORDER BY id", (code,)).fetchall()

    def runs_started(self, c, code: str, entry_id: int) -> int:
        return c.execute("SELECT COUNT(*) FROM match_lanes l JOIN matches m ON m.id=l.match_id WHERE m.hosted_code=? AND l.entry_id=? "
                         "AND m.state!='ready'", (code, entry_id)).fetchone()[0]

    def runs_left(self, c, h: dict, e: dict) -> int:
        return e["allowed_runs"] - max(e["runs"], self.runs_started(c, h["code"], e["entry_id"]))

    def list(self) -> List[dict]:
        """For Match Control: each hosted match, its entrants (placed where, runs left) and its matches here."""
        c, out = self.db.conn(), []
        for h in sorted(self.by_code.values(), key=lambda h: (h.get("starts_ms") or h.get("opens_ms") or 0, h["name"])):
            placed = {}
            for r in c.execute("SELECT l.entry_id, l.device_id, l.lane, m.id, m.state FROM match_lanes l JOIN matches m ON m.id=l.match_id "
                               "WHERE m.hosted_code=? AND m.state IN ('ready','running','paused','holding','overtime')", (h["code"],)):
                placed[r["entry_id"]] = {"device_id": r["device_id"], "lane": r["lane"], "match_id": r["id"], "state": r["state"]}
            out.append({k: h.get(k) for k in ("code", "name", "timing", "starts_ms", "late_ms", "opens_ms", "closes_ms", "attempts", "classes", "official")}
                       | {"format_name": h["format"].get("name", ""), "run_ms": run_ms(h["format"]),
                          "entrants": [dict(e, placed=placed.get(e["entry_id"]), left=self.runs_left(c, h, e)) for e in h.get("entrants", [])],
                          "matches": [{"id": m["id"], "state": m["state"]} for m in self.local(c, h["code"])]})
        return out

    # ------------------------------------------------------------------------------------------------ lanes
    def _key_of(self, c, device_id: str) -> Optional[str]:
        r = c.execute("SELECT k.key_hex FROM targets t JOIN target_keys k ON k.fingerprint=t.key_fp WHERE t.device_id=?", (device_id,)).fetchone()
        return r["key_hex"] if r else None

    def _joinable(self, h: dict, m, now: int) -> bool:
        """A local match of this hosted match that takes another lane now."""
        if m["state"] == "ready":
            return True
        return h["timing"] == "sync" and m["state"] in ("running", "holding") and now < h["starts_ms"] + (h["late_ms"] or 0)

    def place(self, code: str, lanes, by: str) -> int:
        """Put entrants on targets: [{"device_id", "entry_id"}]. Into this hosted match's match here that has not
        started (or, synchronized, one still taking late entrants), else a new one. Returns the match id."""
        h = self.by_code.get(code)
        if h is None:
            raise MatchError("this range does not host that match (or deepTENS has not said so yet)")
        if h.get("official"):
            raise MatchError("its results are official")
        if not isinstance(lanes, list) or not lanes:
            raise MatchError("choose at least one entrant and target")
        now = self.core.now_ms()
        rms = run_ms(h["format"])
        if h["timing"] == "sync":
            if now < h["starts_ms"] - JOIN_BEFORE_MS:
                raise MatchError("lanes can be given from 30 minutes before the start")
            if now >= h["starts_ms"] + (h["late_ms"] or 0):
                raise MatchError("too late: the match has started")
        elif not h["opens_ms"] <= now <= h["closes_ms"] - rms:
            raise MatchError("not now: a run must lie within the match's window")
        entrants = {e["entry_id"]: e for e in h.get("entrants", [])}
        c = self.db.conn()
        seen_e, seen_d = set(), set()
        for ln in lanes:
            if not isinstance(ln, dict) or ln.get("entry_id") not in entrants:
                raise MatchError("an entrant of this match at this range, please")
            if ln["entry_id"] in seen_e or ln.get("device_id") in seen_d:
                raise MatchError("each entrant and each target once")
            seen_e.add(ln["entry_id"])
            seen_d.add(ln.get("device_id"))
            if not isinstance(ln.get("device_id"), str) or self._key_of(c, ln["device_id"]) is None:
                raise MatchError(f"{ln.get('device_id')}: only a target with FeT target firmware and its key can shoot a deepTENS match")
            if self.runs_left(c, h, entrants[ln["entry_id"]]) <= 0:
                raise MatchError(f"{entrants[ln['entry_id']]['athlete']['public_name']} has no run left")
            if c.execute("SELECT 1 FROM match_lanes l JOIN matches m ON m.id=l.match_id WHERE m.hosted_code=? AND l.entry_id=? "
                         "AND m.state IN ('ready','running','paused','holding','overtime')", (code, ln["entry_id"])).fetchone():
                raise MatchError(f"{entrants[ln['entry_id']]['athlete']['public_name']} is on a lane already")
        mc = self.core.matches
        target = next((m for m in self.local(c, code) if self._joinable(h, m, now)), None)
        name = lambda e: e["athlete"]["public_name"]
        if target is None:
            fmt = mf.validate(copy.deepcopy(h["format"]))
            mid = mc.create(h["name"], None, [{"device_id": ln["device_id"], "competitor": name(entrants[ln["entry_id"]])} for ln in lanes],
                            by, fmt=fmt, paced="spoken", publish=False)
            with self.db.write() as w:
                w.execute("UPDATE matches SET hosted_code=? WHERE id=?", (code, mid))
                for ln in lanes:
                    w.execute("UPDATE match_lanes SET entry_id=? WHERE match_id=? AND device_id=?", (ln["entry_id"], mid, ln["device_id"]))
        else:
            mid = target["id"]
            for ln in lanes:
                if target["state"] == "ready":
                    mc.add_ready(mid, ln["device_id"], name(entrants[ln["entry_id"]]), by)
                else:
                    mc.add_late(mid, ln["device_id"], name(entrants[ln["entry_id"]]), by, hosted_ok=True)
                with self.db.write() as w:
                    w.execute("UPDATE match_lanes SET entry_id=? WHERE match_id=? AND device_id=?", (ln["entry_id"], mid, ln["device_id"]))
        mc._pub(mid)
        if self.core.deeptens is not None:
            self.core.deeptens.wake.set()               # its targets (and keys) go to deepTENS at once
        return mid

    def signed_in(self, device_id: str, athlete_id: int) -> Optional[int]:
        """An athlete signed in at a lane: if they are an entrant here of a hosted match that takes a lane now, they
        are put on it. Returns the match id, or None."""
        now = self.core.now_ms()
        for h in self.by_code.values():
            e = next((x for x in h.get("entrants", []) if x["athlete"]["athlete_id"] == athlete_id), None)
            if e is None:
                continue
            try:
                return self.place(h["code"], [{"device_id": device_id, "entry_id": e["entry_id"]}], "sign-in")
            except MatchError:
                continue
        return None

    # ------------------------------------------------------------------------------------------------ the clock
    def tick(self) -> None:
        """Start synchronized matches at deepTENS's start time (back-dated by the tick's delay)."""
        now = self.core.now_ms()
        for r in self.db.conn().execute("SELECT id, hosted_code FROM matches WHERE hosted_code IS NOT NULL AND state='ready'").fetchall():
            h = self.by_code.get(r["hosted_code"])
            if h and h["timing"] == "sync" and h["starts_ms"] <= now < h["starts_ms"] + run_ms(h["format"]):
                self.core.matches.start(r["id"], "deepTENS", at=h["starts_ms"])

    # ------------------------------------------------------------------------------------------------ supervision
    def console_seen(self, mid: int, role: str) -> None:
        """Staff have the match open in Match Control (its page reloads every 5 s at most)."""
        if role not in ("director", "officer", "admin"):
            return
        now = self.core.now_ms()
        with self.db.write() as c:
            m = c.execute("SELECT hosted_code, state FROM matches WHERE id=?", (mid,)).fetchone()
            if m is None or not m["hosted_code"] or m["state"] in ("done", "aborted"):
                return
            last = c.execute("SELECT rowid, to_ms FROM hosted_console WHERE match_id=? ORDER BY to_ms DESC LIMIT 1", (mid,)).fetchone()
            if last is not None and now - last["to_ms"] < CONSOLE_GAP_MS:
                c.execute("UPDATE hosted_console SET to_ms=? WHERE rowid=?", (max(now, last["to_ms"]), last["rowid"]))
            else:
                c.execute("INSERT INTO hosted_console (match_id, from_ms, to_ms) VALUES (?,?,?)", (mid, now, now))

    def unsupervised(self, c, mid: int, start: int, end: int) -> list:
        """The parts of [start, end) when no staff had the match open, each CONSOLE_GAP_MS or longer (console_seen
        already joins sightings closer than that)."""
        gaps, t = [], start
        for r in c.execute("SELECT from_ms, to_ms FROM hosted_console WHERE match_id=? AND to_ms>=? AND from_ms<? ORDER BY from_ms",
                           (mid, start, end)):
            if r["from_ms"] - t >= CONSOLE_GAP_MS:
                gaps.append([t, min(r["from_ms"], end)])
            t = max(t, r["to_ms"])
        if end - t >= CONSOLE_GAP_MS:
            gaps.append([t, end])
        return gaps

    # ------------------------------------------------------------------------------------------------ to deepTENS
    def started_ms(self, c, m) -> Optional[int]:
        r = c.execute("SELECT at_ms FROM match_log WHERE match_id=? AND kind='phase_start' AND phase=0 ORDER BY id LIMIT 1", (m["id"],)).fetchone()
        return r["at_ms"] if r else None

    def runs(self, mid: int) -> list:
        """(key, data) for each lane of a started hosted match."""
        c = self.db.conn()
        m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        if m is None or not m["hosted_code"] or m["state"] == "ready":
            return []
        start = self.started_ms(c, m)
        if start is None:
            return []
        fmt = json.loads(m["format_json"])
        end = start + run_ms(fmt)
        now = self.core.now_ms()
        gaps = self.unsupervised(c, mid, start, min(end, now)) if now < end else self.unsupervised(c, mid, start, end)
        out = []
        for ln in c.execute("SELECT * FROM match_lanes WHERE match_id=? AND entry_id IS NOT NULL ORDER BY lane", (mid,)):
            acts = [{"at_ms": r["at_ms"], "kind": r["kind"], "phase": r["phase"]} for r in
                    c.execute("SELECT at_ms, kind, phase FROM match_log WHERE match_id=? AND device_id=? AND kind IN ('start_record','to_sighters') ORDER BY id",
                              (mid, ln["device_id"]))]
            out.append((f"h{mid * 1000 + ln['lane']}", {
                "match": m["hosted_code"], "entry_id": ln["entry_id"], "lane": ln["fp"] or ln["lane"], "device_id": ln["device_id"],
                "started_ms": start, "ends_ms": end, "stopped_ms": None, "supervised": not gaps, "unsupervised": gaps, "actions": acts}))
        return out

    def carried(self, c) -> list:
        """(device_id, from_ms, to_ms) of every lane in a hosted match here: its lines go to deepTENS."""
        out = []
        for r in c.execute("SELECT l.device_id, m.id, m.state, m.format_json FROM match_lanes l JOIN matches m ON m.id=l.match_id "
                           "WHERE m.hosted_code IS NOT NULL AND l.entry_id IS NOT NULL"):
            m = c.execute("SELECT * FROM matches WHERE id=?", (r["id"],)).fetchone()
            start = self.started_ms(c, m)
            h = self.by_code.get(m["hosted_code"])
            if start is None:                          # not started: from now until its (planned) end
                start = (h or {}).get("starts_ms") or self.core.now_ms()
            out.append((r["device_id"], start - LINE_EARLY_MS, start + run_ms(json.loads(r["format_json"]))))
        return out

    def targets_due(self) -> list:
        """The targets of lanes in hosted matches, with their keys (for deepTENS's /targets)."""
        c, out, seen = self.db.conn(), [], set()
        for did, _, _ in self.carried(c):
            if did in seen:
                continue
            seen.add(did)
            t = c.execute("SELECT t.device_id, t.name, t.lane, t.key_fp, t.face_id, t.board_rev, k.key_hex FROM targets t "
                          "JOIN target_keys k ON k.fingerprint=t.key_fp WHERE t.device_id=?", (did,)).fetchone()
            if t is None:
                continue
            out.append({"device_id": t["device_id"], "lane": t["lane"], "name": t["name"] or "", "board_rev": t["board_rev"],
                        "face": t["face_id"] or "issf-ar10", "key_fp": t["key_fp"], "key_hex": t["key_hex"]})
        return out

    def lines_due(self, limit: int = 200) -> list:
        """Signed lines of those targets in those spans not sent yet, oldest first."""
        c, out = self.db.conn(), []
        for did, a, b in self.carried(c):
            for r in c.execute("SELECT e.* FROM events e LEFT JOIN deeptens_lines d ON d.event_id=e.id WHERE e.device_id=? AND e.type IN (?,?) "
                               "AND e.event_ms>=? AND e.event_ms<? AND d.event_id IS NULL ORDER BY e.id LIMIT ?",
                               (did, *LINE_TYPES, a, b, limit)):
                out.append(r)
        out.sort(key=lambda r: r["id"])
        return out[:limit]
