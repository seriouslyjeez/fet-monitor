"""fetcore stays pure: the scoring brain FeT Monitor and deeptens.com share imports only the Python standard library
and itself. No database, no network, no FeT Monitor server code (docs/PLAN-deeptens.md, "One scoring brain")."""
import ast
import sys
import unittest
from pathlib import Path

FETCORE = Path(__file__).resolve().parent.parent / "fetcore"
STDLIB = set(getattr(sys, "stdlib_module_names", ())) or {
    "__future__", "math", "re", "json", "hmac", "hashlib", "typing", "dataclasses", "decimal", "functools", "itertools",
    "statistics", "collections", "enum", "fractions", "copy", "bisect", "string", "datetime", "time", "struct"}
FORBIDDEN = {"sqlite3", "socket", "http", "urllib", "threading", "subprocess", "os", "shutil", "asyncio", "ssl"}


def imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            yield ("." if node.level else (node.module or "").split(".")[0]), node.lineno


class Pure(unittest.TestCase):
    def test_only_the_standard_library_and_itself(self):
        files = sorted(FETCORE.glob("*.py"))
        self.assertGreaterEqual(len(files), 12)
        for f in files:
            for mod, line in imports(f):
                with self.subTest(file=f.name, line=line, module=mod):
                    self.assertNotIn(mod, FORBIDDEN, "no database, network, files or processes in fetcore")
                    self.assertTrue(mod in (".", "fetcore") or mod in STDLIB, f"{mod} is not the standard library")

    def test_installable_layout(self):
        text = (FETCORE / "pyproject.toml").read_text()
        self.assertIn('package-dir = {"fetcore" = "."}', text)
        self.assertIn('version = {attr = "fetcore.__version__"}', text)


class Facts:
    """A match's shots kept in memory, as deeptens might hold them: no database."""
    def __init__(self):
        self.rows = {}

    def shots(self, sid):
        return sorted(self.rows.get(sid, []), key=lambda s: (s["event_ms"], s["id"]))

    def unplaced(self, sid):
        return []

    def decisions(self):
        return {}

    def corrections(self):
        return []


class EngineWithoutADatabase(unittest.TestCase):
    """The match engine runs on plain dicts: a match is started, ticks through its phases, takes shots and is scored."""
    def test_a_small_match_from_start_to_result(self):
        import json
        from fetcore import matchengine as me
        from fetcore.matchrun import Run
        fmt = {"name": "Tiny", "book": "ISSF", "face": "issf-ar10", "scoring": "decimal",
               "phases": [{"kind": "sight", "label": "S", "seconds": 100, "shots": 0, "position": "", "warnings": [], "start": "", "end": ""},
                          {"kind": "record", "label": "R", "seconds": 300, "shots": 2, "position": "", "warnings": [], "start": "", "end": ""}]}
        m = {"id": 1, "name": "Tiny", "format_json": json.dumps(fmt), "state": "ready", "phase": -1, "phase_started_ms": None,
             "phase_used_ms": 0, "phase_extra_ms": 0, "paced": "spoken", "started_ms": None, "ended_ms": None, "final_kind": None,
             "official_ms": None}
        lane = {"lane": 1, "device_id": "t1", "competitor": "Ames", "series_id": 7, "moving_since_ms": None, "moving_left_ms": None,
                "extra_ms": 0, "own_phase": None, "own_left_ms": None, "own_since_ms": None, "sighting": 0, "start_phase": 0,
                "carry_shots": 0, "carry_ms": None, "from_match": None, "left_ms": None, "to_match": None, "out_place": None, "out_ms": None}
        facts, log, t0 = Facts(), [], 1_000_000
        state = {"m": m, "lanes": [lane]}

        def step(fn):                                  # what a server does: load, run, store
            run = Run(state["m"], state["lanes"], log, facts, close_ms=None)
            fn(run)
            state["m"] = dict(state["m"], **run.match_changes())
            for dev, cols in run.lane_changes():
                state["lanes"] = [dict(x, **cols) if x["device_id"] == dev else x for x in state["lanes"]]
            log.extend(run.new_log)
            return run

        run = step(lambda r: r.start("ro", t0))
        self.assertEqual(run.modes, [("t1", "sighter", "Ames", t0)])
        run = step(lambda r: r.tick(t0 + 100_500))      # sighting ends at exactly 100 s
        self.assertEqual((state["m"]["phase"], run.modes), (1, [("t1", "record", "Ames", t0 + 100_000)]))
        shot = lambda i, t, tenths: {"id": i, "event_ms": t, "stream": "scored", "series_no": i, "is_miss": 0, "tenths": tenths,
                                    "ring": tenths // 10, "inner": 0, "ax_h": 0, "ay_h": 0, "misfit_um": None}
        facts.rows[7] = [shot(1, t0 + 150_000, 104), shot(2, t0 + 160_000, 99), shot(3, t0 + 450_000, 109)]   # the last: after the end
        step(lambda r: r.tick(t0 + 400_001))
        self.assertEqual(state["m"]["state"], "done")
        flags = me.flags(fmt, log, state["lanes"], {7: facts.shots(7)}, {}, {}, {1: me.correction_effects(fmt, [], 1)}, None)
        self.assertEqual([(f["shot_id"], f["code"]) for f in flags], [(3, "after_end")])
        counted = me.counted(fmt, state["lanes"][0], facts.shots(7), [], {f["shot_id"]: f for f in flags}, me.correction_effects(fmt, [], 1), None)
        self.assertEqual(me.entry_view(counted, True)["total"], 20.3)


if __name__ == "__main__":
    unittest.main()
