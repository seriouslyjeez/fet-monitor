"""Competitions over HTTP: director-only changes, public results, CSV exports, the scoreboard feed."""
import csv
import io
import json

from tests.test_match_http import BASE, MatchHttpBase

SHOT = '{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":0.00 , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'


class CompetitionHttp(MatchHttpBase):
    def test_competition_flow(self):
        self.login("md", "director-pass")
        self.login("ro", "officer-pass")
        fid = self.fmt()
        self.assertEqual(self.req("POST", "/api/v1/match/competitions", {"name": "X", "format_id": fid}, "ro")[0], 403)
        st, c, _ = self.req("POST", "/api/v1/match/competitions", {"name": "League", "format_id": fid, "team_size": 2}, "md")
        self.assertEqual((st, c["team_size"], c["paced"]), (200, 2, "spoken"))
        cid = c["id"]
        self.assertEqual(self.req("POST", f"/api/v1/match/competitions/{cid}/competitors", {"family": "x"}, "ro")[0], 403)
        for fam, team in (("=HYPERLINK(1)", "Red"), ("Brown", "Red")):
            st, c, _ = self.req("POST", f"/api/v1/match/competitions/{cid}/competitors", {"family": fam, "given": "A", "team": team}, "md")
            self.assertEqual(st, 200, c)
        ids = [p["id"] for p in c["competitors"]]
        self.assertEqual(self.req("POST", f"/api/v1/match/competitions/{cid}/competitors", {"family": ""}, "md")[0], 400)
        st, c, _ = self.req("POST", f"/api/v1/match/competitions/{cid}/relays",
                            {"lanes": [{"device_id": "lane-1", "competitor_id": ids[0]}, {"device_id": "lane-2", "competitor_id": ids[1]}]}, "md")
        self.assertEqual((st, len(c["relays"])), (200, 1))
        mid = c["relays"][0]["match_id"]
        self.req("POST", f"/api/v1/match/matches/{mid}/start", {}, "ro")
        self.clock.t = BASE + 5000
        for i, (did, x) in enumerate((("lane-1", "0.00"), ("lane-1", "0.00"), ("lane-2", "2.00"), ("lane-2", "2.00")), 1):
            n = 1 if i in (1, 3) else 2
            self.core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": 5000, "events": [
                {"seq": n, "type": "shot", "uptime_ms": 5000, "raw": SHOT % (n, x)}]})
        # public, no login
        st, res, _ = self.req("GET", f"/api/v1/results/{cid}", header=False)
        ind = res["classes"][0]["individuals"]
        self.assertEqual((st, [e["family"] for e in ind], ind[0]["place"]), (200, ["=HYPERLINK(1)", "Brown"], 1))
        self.assertEqual(res["classes"][0]["teams"][0]["place"], 1)
        self.assertEqual(self.req("GET", "/api/v1/results/999", header=False)[0], 404)
        st, ctype, disp, body = self.raw(f"/api/v1/results/{cid}.csv")
        self.assertEqual((st, ctype), (200, "text/csv; charset=utf-8"))
        self.assertIn("attachment", disp)
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM for spreadsheets")
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual(rows[0][:5], ["Competition", "Class", "Place", "Family name", "Given name"])
        self.assertEqual(rows[1][3], "'=HYPERLINK(1)", "a formula is kept as text")
        st, _, _, tbody = self.raw(f"/api/v1/results/{cid}-teams.csv")
        trows = list(csv.reader(io.StringIO(tbody.decode("utf-8-sig"))))
        self.assertEqual((trows[1][2], trows[1][3]), ("1", "Red"))
        st, b, _ = self.req("GET", f"/api/v1/board?c={cid}", header=False)
        self.assertEqual((b["competitions"][0]["name"], b["matches"]), ("League", []))
        st, lst, _ = self.req("GET", "/api/v1/competitions", header=False)
        self.assertEqual([x["name"] for x in lst["competitions"]], ["League"])
        self.assertEqual(self.req("GET", "/api/v1/board?c=x", header=False)[0], 400)



class CompetitorImportHttp(MatchHttpBase):
    def upload(self, path, body, who, ctype="text/csv"):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        h = {"X-FET-Admin": "1", "Content-Type": ctype}
        if who in self.cookies:
            h["Cookie"] = self.cookies[who]
        c.request("POST", path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        c.close()
        return out

    def test_upload(self):
        import os
        self.login("md", "director-pass")
        self.login("ro", "officer-pass")
        st, comp, _ = self.req("POST", "/api/v1/match/competitions", {"name": "L", "format_id": self.fmt()}, "md")
        url = f"/api/v1/match/competitions/{comp['id']}/competitors/import"
        before = sorted(os.listdir(self.dir))
        st, body = self.upload(url, "\ufeffFamily,Given\nSmith,Ann\nBöhm,Jörg\n".encode("utf-8"), "md")
        self.assertEqual((st, body["added"], [p["family"] for p in body["competitors"]]), (200, 2, ["Böhm", "Smith"]))
        st, body = self.upload(url, "Müller,Ute\n".encode("cp1252"), "md")
        self.assertEqual((st, body["competitors"][1]["family"]), (200, "Müller"), "an older Windows file")
        self.assertEqual(sorted(os.listdir(self.dir)), before, "nothing written next to the database")
        st, body = self.upload(url, b",nobody\n", "md")
        self.assertEqual(st, 400)
        self.assertIn("line 1", body["error"])
        self.assertEqual(self.upload(url, b"X,y\n", "ro")[0], 403, "directors only")
        self.assertEqual(self.upload(url, b"X,y\n", "nobody")[0], 401)
        self.assertEqual(self.upload(url, b"x" * (256 * 1024 + 1), "md")[0], 413)


class CompetitorMoveHttp(MatchHttpBase):
    def test_director_moves_before_the_start(self):
        self.login("md", "director-pass"); self.login("ro", "officer-pass")
        st, comp, _ = self.req("POST", "/api/v1/match/competitions", {"name": "League", "format_id": self.fmt()}, "md")
        cid = comp["id"]
        ids = []
        for fam in ("Ames", "Bell"):
            st, d, _ = self.req("POST", f"/api/v1/match/competitions/{cid}/competitors", {"family": fam}, "md")
            ids.append(next(p["id"] for p in d["competitors"] if p["family"] == fam))
        st, d, _ = self.req("POST", f"/api/v1/match/competitions/{cid}/relays", {"lanes": [{"device_id": "lane-1", "competitor_id": ids[0]},
                                                                                          {"device_id": "lane-2", "competitor_id": ids[1]}]}, "md")
        rid = d["relays"][0]["match_id"]
        path = f"/api/v1/match/competitions/{cid}/competitors/{ids[0]}/move"
        st, _, _ = self.req("POST", path, {"match_id": rid, "device_id": "lane-2"}, "ro")
        self.assertEqual(st, 403)
        st, d, _ = self.req("POST", path, {"match_id": rid, "device_id": "lane-2"}, "md")
        self.assertEqual((st, {l["device_id"]: l["competitor"] for l in d["relays"][0]["lanes"]}), (200, {"lane-1": "BELL", "lane-2": "AMES"}))
        st, d, _ = self.req("POST", path, {"match_id": None}, "md")
        self.assertEqual(st, 200)
        st, d, _ = self.req("POST", path, {"match_id": rid, "device_id": "lane-1"}, "md")
        self.assertEqual((st, d["error"]), (400, "BELL is on that target: move them first"))


class DeleteHttp(MatchHttpBase):
    def setUp(self):
        super().setUp()
        self.login("md", "director-pass"); self.login("ro", "officer-pass")
        st, d, _ = self.req("POST", "/api/v1/match/matches", {"name": "Night", "format_id": self.fmt(), "lanes": [{"device_id": "lane-1", "competitor": "Ames"}]}, "md")
        self.mid = d["id"]
        self.req("POST", f"/api/v1/match/matches/{self.mid}/start", {}, "md")

    def test_single_match_results_are_public(self):
        st, body, _ = self.req("GET", "/api/v1/results", header=False)
        self.assertEqual((st, [r["key"] for r in body["results"]]), (200, [f"m{self.mid}"]))
        st, res, _ = self.req("GET", f"/api/v1/results/m{self.mid}", header=False)
        self.assertEqual((st, res["classes"][0]["individuals"][0]["name"]), (200, "Ames"))
        st, _, disp, _ = self.raw(f"/api/v1/results/m{self.mid}.csv")
        self.assertEqual((st, disp), (200, f'attachment; filename="results-m{self.mid}.csv"'))

    def test_delete_results_then_the_match(self):
        key = f"m{self.mid}"
        self.assertEqual(self.req("POST", "/api/v1/match/results/delete", {"keys": [key]}, "ro")[0], 403)
        self.assertEqual(self.req("POST", "/api/v1/match/results/delete", {"keys": [key]})[0], 401)
        st, body, _ = self.req("POST", "/api/v1/match/results/delete", {"keys": [key]}, "md")
        self.assertEqual((st, body["count"], body["results"][0]["hidden"]), (200, 1, True))
        self.assertEqual(self.req("GET", f"/api/v1/results/{key}", header=False)[0], 404)
        self.assertEqual(self.raw(f"/api/v1/results/{key}.csv")[0], 404)
        self.assertEqual(self.req("POST", "/api/v1/match/results/restore", {"keys": [key]}, "md")[0], 200)
        self.assertEqual(self.req("GET", f"/api/v1/results/{key}", header=False)[0], 200)
        st, body, _ = self.req("POST", "/api/v1/match/delete", {"matches": [self.mid]}, "md")
        self.assertEqual((st, body["error"]), (400, "'Night' is running: stop it before deleting"))
        self.req("POST", f"/api/v1/match/matches/{self.mid}/command", {"action": "abort"}, "md")
        self.assertEqual(self.req("POST", "/api/v1/match/delete", {"matches": [self.mid]}, "ro")[0], 403)
        st, body, _ = self.req("POST", "/api/v1/match/delete", {"matches": [self.mid]}, "md")
        self.assertEqual((st, body), (200, {"deleted": 1, "frozen": 1}))
        st, body, _ = self.req("GET", "/api/v1/results", header=False)
        frozen = body["results"][0]
        self.assertEqual((frozen["kind"], frozen["name"]), ("frozen", "Night"))
        st, res, _ = self.req("GET", f"/api/v1/results/{frozen['key']}?shots=1", header=False)
        self.assertEqual((st, res["classes"][0]["individuals"][0]["name"]), (200, "Ames"))
        self.assertEqual(self.req("GET", f"/api/v1/match/matches/{self.mid}", None, "md")[0], 400)

    def test_deleted_competition_results_are_not_public(self):
        cc = self.core.competitions
        cid = cc.create("League", self.fmt(), "md")
        st, body, _ = self.req("GET", f"/api/v1/board?c={cid}", header=False)
        self.assertEqual([c["name"] for c in body["competitions"]], ["League"])
        self.core.results.delete_results([f"c{cid}"], "md")
        st, body, _ = self.req("GET", f"/api/v1/board?c={cid}", header=False)
        self.assertEqual((st, body["competitions"]), (200, []), "not on the TV scoreboard")
        st, body, _ = self.req("GET", "/api/v1/competitions", header=False)
        self.assertEqual(body["competitions"], [], "nor offered for it")
