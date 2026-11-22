"""tools/release_status.py: what is published, what is in the code, what to do (user 2026-11-14: releases were
described as unpublished after they had been published). git and GitHub stand-ins; the real versions are read."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_status", ROOT / "tools/release_status.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


class Status(unittest.TestCase):
    def run_with(self, tags, log, reviewed=None):
        def git(*args):
            if args[0] == "log":
                return log.get(args[2].split("..")[0], "")
            return ""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "r.json")
            f.write_text(json.dumps(reviewed or {}))
            with mock.patch.object(rs, "git", git), mock.patch.object(rs, "REVIEWED", f):
                return {r["product"]: r for r in rs.status(set(tags))}

    def test_every_case(self):
        code = {r["product"]: r["code"] for r in self.run_with([], {}).values()}
        self.assertTrue(all(v != "?" for v in code.values()), "every product's version is read from the code: %s" % code)
        srv, disp, br = code["FeT Monitor (server)"], code["FeT Display (CYD)"], code["FeT Bridge (ESP-01)"]
        fw, newer = "fw-" + br, "fw-%d.0.0" % (int(br.split(".")[0]) + 1)
        rows = self.run_with(["v" + srv, "v1.0.0", "display-" + disp, fw, "fw-0.4.9", "v1.0.0^{}"],
                             {"v" + srv: "", "display-" + disp: "abc1234 a display change\n", fw: "def5678 a bridge change\n"})
        self.assertEqual(rows["FeT Monitor (server)"]["todo"], "published; nothing new")
        self.assertIn("bump the version", rows["FeT Display (CYD)"]["todo"], "changed after its published version")
        self.assertEqual(rows["FeT Bridge (ESP-01)"]["published"], fw, "the newest by version, not by name")
        self.assertIn("not published yet: ready to release", self.run_with(["v1.0.0"], {})["FeT Monitor (server)"]["todo"])
        rev = {"FeT Bridge (ESP-01)": {"tag": fw, "through": "def5678", "why": "same image"}}
        rows = self.run_with([fw], {fw: "def5678 a bridge change\n", "def5678": ""}, rev)
        self.assertIn("reviewed: no release needed (same image)", rows["FeT Bridge (ESP-01)"]["todo"])
        rows = self.run_with([fw], {"def5678": "9999999 a later change\n"}, rev)
        self.assertIn("1 commit(s) since def5678: bump the version", rows["FeT Bridge (ESP-01)"]["todo"], "a later commit shows again")
        rows = self.run_with([newer], {newer: "", "def5678": "x\n"}, rev)
        self.assertEqual(rows["FeT Bridge (ESP-01)"]["todo"], "the code says %s, older than the newest %s" % (br, newer[3:]),
                         "a review of an older release does not count")

    def test_the_reviewed_file_names_real_products(self):
        data = json.loads((ROOT / "tools/release_reviewed.json").read_text())
        for k, v in data.items():
            if k.startswith("_"):
                continue
            self.assertIn(k, rs.PRODUCTS)
            self.assertTrue(v["tag"].startswith(rs.PRODUCTS[k][0]) and v["through"] and v["why"])


if __name__ == "__main__":
    unittest.main()
