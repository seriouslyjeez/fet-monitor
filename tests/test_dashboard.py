"""Feeds real server output through the dashboard's page logic in Node (skipped if Node is absent)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from fetserver.core import Core
from fetserver.db import Database

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 1_000_000_000
ALICE, BOB = "alice-client-01", "bob-client-0002"
SHOT = ('{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":%s , "N":1, "E":2, "S":3, "W":0 , '
        '"V_REF":4.9, , "T":19.00, , "VERSION":"4.3.0" }')
MISS = '{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class DashboardLogic(unittest.TestCase):
    def test_page_logic_against_real_api_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [BASE]
            core = Core(Database(os.path.join(tmp, "t.db")), now_ms=lambda: now[0], lease_ms=10**9)
            hb = lambda did, name, **kw: core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": now[0] - BASE, "name": name, **kw})
            seq = [0]

            def shoot(did, x="0.00", y="0.00", raw=None):
                seq[0] += 1
                now[0] += 1000
                core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": now[0] - BASE, "events": [
                    {"seq": seq[0], "type": "shot", "uptime_ms": now[0] - BASE, "raw": raw or SHOT % (seq[0], x, y)}]})

            hb("lane-1", "Lane 1")
            hb("lane-2", "<img src=x onerror=alert(1)>", queue_depth=3)
            hb("lane-3", "Lane 3")
            hb("lane-4", "Lane 4")
            # lane-1: a match. Sighters, then 13 record shots with inner tens and a miss; an older closed series too.
            core.acquire_control("lane-1", ALICE, "Alice")
            core.set_mode("lane-1", ALICE, "record")
            for i in range(25):
                shoot("lane-1", "1.00", "1.00")
            core.set_mode("lane-1", ALICE, "sighter", new_series=True)
            for i in range(3):
                shoot("lane-1", "6.00", "0.00")
            core.set_mode("lane-1", ALICE, "record")
            for x in ("0.00", "2.00", "2.60", "3.10", "1.00", "4.20", "0.50", "2.80", "0.30", "6.00", "0.00"):
                shoot("lane-1", x, "0.00")
            shoot("lane-1", raw=MISS % 99)
            shoot("lane-1", "2.00", "0.00")
            # lane-2: practice, hostile names, 12 shots
            core.acquire_control("lane-2", BOB, "Bob <script>alert(1)</script>")
            core.set_mode("lane-2", BOB, "practice", shooter="<b>Mallory</b>")
            for i in range(12):
                shoot("lane-2", "2.50", "-1.00")
            # lane-4: sighter with a few shots, a hostile shooter name
            core.acquire_control("lane-4", ALICE, "Alice")
            core.set_mode("lane-4", ALICE, "sighter", shooter="<u>Eve</u>")
            for x in ("1.00", "9.00", "-3.00"):
                shoot("lane-4", x, "0.00")
            # lane-5: an ammo test with a 5.7 mm projectile, 14 shots, one miss, hostile label
            hb("lane-5", "Lane 5")
            core.acquire_control("lane-5", ALICE, "Alice")
            core.set_settings("lane-5", ALICE, projectile_mm=5.7)
            core.set_mode("lane-5", ALICE, "ammo", label="<i>Lot 7</i>")
            for dx, dy in ((0, 0), (1.1, 0.4), (-0.8, 1.2), (0.3, -1.5), (2.0, 0.9), (-1.4, -0.6), (0.7, 1.9), (-0.2, 0.1),
                           (1.6, -1.1), (-1.9, 0.8), (0.4, 0.4), (1.0, 1.0), (-0.5, -1.0)):
                shoot("lane-5", "%.2f" % (12.0 + dx), "%.2f" % (-8.0 + dy))
            shoot("lane-5", raw=MISS % 98)
            now[0] += 60_000                                    # lane-3 never reported again: offline
            for did, name in (("lane-1", "Lane 1"), ("lane-2", "<img src=x onerror=alert(1)>"), ("lane-4", "Lane 4"), ("lane-5", "Lane 5")):
                hb(did, name, **({"queue_depth": 3} if did == "lane-2" else {}))
            targets = core.list_targets(ALICE)
            series = core.list_series("lane-1")
            closed = next(s["id"] for s in series if not s["open"])
            # a competition on two extra targets (after the targets snapshot, so the pages above are unchanged)
            for did in ("comp-1", "comp-2", "comp-3"):
                hb(did, did)
            cc = core.competitions
            fid = core.matches.save_format({"name": "Comp", "book": "ISSF", "scoring": "decimal", "phases": [{"kind": "record", "label": "R", "seconds": 600, "shots": 12}]}, "md")["id"]
            cid = cc.create("<b>League</b>", fid, "md", team_size=2)
            ids = [cc.save_competitor(cid, {"family": f, "given": g, "class": k, "team": t}) for f, g, k, t in
                   (("Ames", "<i>Al</i>", "Open", "Red"), ("Brown", "Bo", "Open", "Red"), ("Cole", "Cy", "Junior", "Blue"))]
            rid = cc.add_relay(cid, [{"device_id": f"comp-{i + 1}", "competitor_id": x} for i, x in enumerate(ids)], "md")
            core.matches.start(rid, "ro")
            for did, x in (("comp-1", "0.30"), ("comp-2", "1.50"), ("comp-3", "0.00")):
                for _ in range(12):
                    shoot(did, x, "0.00")
            # corrections: an override and an added shot for Ames, a penalty for Brown, Cole disqualified
            corr = core.corrections
            ames_shots = corr.lane_shots(rid, 1)["shots"]
            corr.add(rid, "override", 1, "md", True, ames_shots[0]["id"], {"value": 9.5, "note": "<b>gauge</b>"})
            corr.add(rid, "added", 1, "md", True, None, {"value": 10.4, "after": 2, "note": "failed to register"})
            corr.add(rid, "penalty", 2, "md", True, None, {"points": 2, "rule": "CMP 7.22.1", "note": "position"})
            corr.add(rid, "dq", 3, "md", True, None, {"rule": "CMP 7.22.2", "official": "<i>J. Doe</i>"})
            corr.add(rid, "protest", 2, "ro", False, corr.lane_shots(rid, 2)["shots"][0]["id"], {"note": "says 10.9"})
            lane_shots = corr.lane_shots(rid, 2)
            comp = core.results.get(f"c{cid}", with_shots=True)
            # comp-1's target fails: its shooter moves to comp-4 (time stopped), then is ready again
            hb("comp-4", "Spare <4>")
            cdetail = cc.detail(cid)
            core.matches.move(rid, "comp-1", "comp-4", "ro")
            now[0] += 30_000
            moving = core.matches.detail(rid)
            core.matches.lane_command(rid, "comp-4", "ready", "ro")
            ready = core.matches.detail(rid)
            mtargets = [{"device_id": t["device_id"], "name": t["name"], "online": t["online"], "in_match": bool(t["match"])}
                        for t in core.list_targets(None)]
            moved_target = next(t for t in core.list_targets(None) if t["device_id"] == "comp-4")
            # a single match on lane-4, stopped, then deleted from Match Control: its results are a saved copy
            sm = core.matches.create("<i>Night</i>", fid, [{"device_id": "lane-4"}], "md")
            core.matches.start(sm, "md")
            shoot("lane-4", "0.50", "0.00")
            single = core.results.get(f"m{sm}", with_shots=True)
            core.matches.command(sm, "abort", "md")
            core.results.delete_matches([sm], [], "md")
            listing = core.results.listing()
            saved = core.results.get(listing[0]["key"])
            lane2 = next(t for t in targets if t["device_id"] == "lane-2")
            from fetserver.rulebookfiles import RulebookFiles
            from fetserver.rulebooks import REPOSITORIES
            store = RulebookFiles(tmp, fetch=lambda url: b"%PDF-1.7 not the real book")
            store.add("cmp-ag-2026", b"%PDF-1.7 x", "upload", "<b>md</b>")
            store.errors["nra-par-2025"] = "cannot reach <b>competitions.nra.org</b>"
            rulebooks = {"docs": store.status(), "repositories": [{"name": n, "url": u} for n, u in REPOSITORIES]}
            payload = {"targets": targets, "series": series, "review": core.series_view(closed, "scored", None, 15),
                       "sight": core.sight_suggestion(lane2["view"]["series_id"], 0), "comp": comp,
                       "moving": moving, "ready": ready, "cdetail": cdetail, "mtargets": mtargets, "movedTarget": moved_target,
                       "single": single, "saved": saved, "listing": listing, "laneShots": lane_shots,
                       "rulebooks": rulebooks}
        proc = subprocess.run(["node", os.path.join(HERE, "dashboard_check.js")], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
