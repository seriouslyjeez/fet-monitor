"""The replay check of docs/PLAN-fetcore-engine.md (step 6): every match a test drove is exported as plain JSON facts
(no database rows, no connection: what deeptens would store), fed to the pure engine (fetcore.matchengine), and must
give the same timeline, clock, flags, unplaced reports and counted shots as FeT Monitor's engine (fetserver/matches.py).
MatchCase runs it after every test; other test cases can call check(testcase, core)."""
import json

from fetcore import matchengine as me


def _plain(rows):
    return json.loads(json.dumps([dict(r) for r in rows]))


def export(c, mid: int) -> dict:
    """One match's facts as plain JSON, read straight from the tables (not through fetserver/matches.py)."""
    m = dict(c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone())
    lanes = _plain(c.execute("SELECT * FROM match_lanes WHERE match_id=? ORDER BY lane", (mid,)))
    series = [ln["series_id"] for ln in lanes if ln["series_id"] is not None]
    facts = {
        "m": m, "fmt": json.loads(m["format_json"]),
        "log": _plain(c.execute("SELECT * FROM match_log WHERE match_id=? ORDER BY id", (mid,))),
        "lanes": lanes,
        "shots": [[s, _plain(c.execute("SELECT * FROM shots WHERE series_id=? ORDER BY event_ms, id", (s,)))] for s in series],
        "unplaced": [[s, _plain(c.execute("SELECT * FROM unplaced WHERE series_id=? ORDER BY event_ms, id", (s,)))] for s in series],
        "decisions": [[r["shot_id"], r["decision"]] for r in c.execute("SELECT shot_id, decision FROM shot_decisions WHERE match_id=?", (mid,))],
        "corrections": _plain(c.execute("SELECT * FROM shot_corrections WHERE match_id=? AND status!='void' ORDER BY id", (mid,))),
    }
    return json.loads(json.dumps(facts))


def pure(facts: dict, now: int, close_ms) -> dict:
    """What the pure engine makes of the facts."""
    fmt, m, log, lanes = facts["fmt"], facts["m"], facts["log"], facts["lanes"]
    shots, unplaced = dict(map(tuple, facts["shots"])), dict(map(tuple, facts["unplaced"]))
    decisions = dict(map(tuple, facts["decisions"]))
    effects = {ln["lane"]: me.correction_effects(fmt, facts["corrections"], ln["lane"]) for ln in lanes}
    flags = me.flags(fmt, log, lanes, shots, unplaced, decisions, effects, close_ms) if m["state"] != "ready" else []
    by_id = {f["shot_id"]: f for f in flags}
    discharged = me.discharged_at(log)
    all_shots = {s["id"]: s for rows in shots.values() for s in rows}
    rows = []
    for ln in lanes:
        eff = effects[ln["lane"]]
        rows.append(me.counted_row(fmt, ln, shots.get(ln["series_id"], []), unplaced.get(ln["series_id"], []), by_id, eff, discharged,
                                   {k: all_shots[k] for k in eff["annulled"] if k in all_shots}))
    return {"windows": me.windows(log), "clock": me.clock(fmt, m, now), "flags": flags,
            "unplaced": me.unplaced_reports(lanes, shots, unplaced, discharged, close_ms), "rows": rows}


def server(core, c, mid: int, now: int) -> dict:
    """What FeT Monitor's engine says about the same match."""
    mm = core.matches
    m = mm._row(c, mid)
    flags = mm.flags(c, m) if m["state"] != "ready" else []
    by_id = {f["shot_id"]: f for f in flags}
    return {"windows": mm.windows(c, mid), "clock": mm._clock(m, now), "flags": flags, "unplaced": mm.unplaced(c, m),
            "rows": [mm.counted_row(c, m, ln, by_id) for ln in mm._lanes(c, mid)]}


def check(tc, core) -> int:
    """Every match in this test's database: the pure engine on exported facts == the server's engine. Returns how many."""
    c = core.db.conn()
    now = core.now_ms()
    ids = [r["id"] for r in c.execute("SELECT id FROM matches ORDER BY id")]
    for mid in ids:
        a = json.loads(json.dumps(pure(export(c, mid), now, core.matches.close_ms), sort_keys=True))
        b = json.loads(json.dumps(server(core, c, mid, now), sort_keys=True))
        for k in b:
            tc.assertEqual(a[k], b[k], f"match {mid}: the pure engine's {k} differ from the server's")
    return len(ids)
