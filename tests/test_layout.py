"""The 1.0 look (docs/PLAN-fetmonitor-1.0.md) in a real browser: headless Chrome (tests/cdp.mjs, no packages) opens the
real page from a real server at phone (390x844), tablet (1180x820) and TV (1920x1080) sizes, light and dark, and checks
what the user decided: no sideways scrolling, controls at least 44 px, the menu on phones, a section switcher instead
of tab strips on phones, the fonts served by the Pi. The colour pairs of the design are checked for contrast (WCAG
4.5:1) from the stylesheet itself. Browser checks are skipped when there is no Chrome or Node."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path

from tests.test_dashboard_patch import chrome
from tests.test_http import ServerCase

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "fetserver/web/dashboard.html").read_text()
PHONE, TABLET, TV = (390, 844), (1180, 820), (1920, 1080)

LIB = r"""
const sleep = ms => new Promise(r => setTimeout(r, ms));
for (let i = 0; i < 80 && !(document.getElementById('app') && document.getElementById('app').children.length); i++) await sleep(50);
await document.fonts.ready;
const shown = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
const box = el => { const r = el.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height)}; };
const small = sel => Array.from(document.querySelectorAll(sel)).filter(shown).filter(el => { const b = box(el); return b.w < 44 || b.h < 44; })
  .map(el => (el.id || el.getAttribute('data-act') || el.textContent.trim().slice(0, 20) || el.tagName) + ' ' + JSON.stringify(box(el)));
const overflow = () => document.documentElement.scrollWidth > document.documentElement.clientWidth;
const CONTROLS = 'header button, header a, #drawer button:not(.scrim), #drawer a, .secsel select, .tabs a';
"""


def node():
    return shutil.which("node")


@unittest.skipIf(chrome() is None or node() is None, "no Chrome or no Node here")
class Layout(ServerCase):
    def look(self, path, size, scheme, script, init=None, cookie=None):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(LIB + script)
        self.addCleanup(os.unlink, f.name)
        env = dict(os.environ)
        if init:
            env["CDP_INIT"] = init
        if cookie:
            env["CDP_COOKIE"] = cookie
        r = subprocess.run([node(), str(ROOT / "tests/cdp.mjs"), chrome(), self.url + path, str(size[0]), str(size[1]), scheme, f.name],
                           capture_output=True, text=True, timeout=90, env=env)
        self.assertTrue(r.stdout, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertNotIn("error", out, out)
        self.assertEqual(out["pageErrors"], [], "no errors in the page")
        return out["value"]

    def test_header_on_every_size_light_and_dark(self):
        script = "return {overflow: overflow(), small: small(CONTROLS), menu: shown(document.getElementById('menubtn')), nav: shown(document.getElementById('topnav')), " \
                 "bg: getComputedStyle(document.body).backgroundColor, font: getComputedStyle(document.body).fontFamily, " \
                 "archivo: document.fonts.check('16px Archivo'), mono: document.fonts.check('16px \"JetBrains Mono\"')};"
        for size in (PHONE, TABLET, TV):
            for scheme in ("light", "dark"):
                with self.subTest(size=size, scheme=scheme):
                    v = self.look("/", size, scheme, script)
                    self.assertFalse(v["overflow"], "no sideways scrolling")
                    self.assertEqual(v["small"], [], "every control at least 44 px")
                    self.assertEqual((v["menu"], v["nav"]), (True, False) if size == PHONE else (False, True),
                                     "a menu button on phones, the links on wider screens")
                    self.assertEqual(v["bg"], "rgb(244, 242, 238)" if scheme == "light" else "rgb(14, 22, 30)", "bone, or the dark ground")
                    self.assertTrue(v["font"].startswith("Archivo"))
                    self.assertTrue(v["archivo"], "Archivo loaded from the Pi")

    def test_the_menu_on_a_phone(self):
        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                v = self.look("/", PHONE, scheme, r"""
const mb = document.getElementById('menubtn'), dr = document.getElementById('drawer');
const before = dr.hidden;
mb.click(); await sleep(60);
const open = !dr.hidden, expanded = mb.getAttribute('aria-expanded'), items = Array.from(dr.querySelectorAll('a.item')).map(a => a.textContent);
const focusIn = dr.contains(document.activeElement), sm = small(CONTROLS), ov = overflow();
const navW = Math.round(dr.querySelector('nav').getBoundingClientRect().width);
document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await sleep(60);
return {before, open, expanded, items, focusIn, small: sm, overflow: ov, navW, closed: dr.hidden, back: document.activeElement === mb,
        expanded2: mb.getAttribute('aria-expanded')};""")
                self.assertEqual((v["before"], v["open"], v["expanded"]), (True, True, "true"))
                self.assertEqual(v["items"], ["Lanes", "Results", "Match Control", "Range Control", "About"])
                self.assertTrue(v["focusIn"], "focus moves into the menu")
                self.assertEqual(v["small"], [])
                self.assertFalse(v["overflow"])
                self.assertLessEqual(v["navW"], 318)
                self.assertEqual((v["closed"], v["back"], v["expanded2"]), (True, True, "false"), "Escape closes it, focus back on the menu button")

    def test_section_switcher_instead_of_tabs_on_phones(self):
        script = r"""
const app = document.getElementById('app');
app.innerHTML = '<div class="panel">' + tabStrip([['a', 'Targets'], ['b', 'Target settings'], ['c', 'TV displays'], ['d', 'Flash a device'], ['e', 'Firmware']], 'c', k => '#/x/' + k, 'Range Control') + '</div>';
await sleep(30);
return {tabs: shown(app.querySelector('.tabs')), sel: shown(app.querySelector('.secsel')), value: app.querySelector('.secsel select').value,
        small: small('.secsel select, .tabs a'), overflow: overflow()};"""
        p = self.look("/", PHONE, "light", script)
        self.assertEqual((p["tabs"], p["sel"], p["value"]), (False, True, "#/x/c"), "phones: a switcher, on the current section")
        self.assertEqual(p["small"], [])
        self.assertFalse(p["overflow"])
        t = self.look("/", TABLET, "light", script)
        self.assertEqual((t["tabs"], t["sel"]), (True, False), "wider screens: the tabs")

    def lane(self, kind="practice", shots=8):
        from fetserver.simtarget import SimTarget
        sim = SimTarget(self.url, "test-token", "lane-1", "Lane 1", seed=3)
        sim.heartbeat()
        self.core.acquire_control("lane-1", "layout-client-0001", "Alice")
        self.core.set_mode("lane-1", "layout-client-0001", {"practice": "practice", "match": "record", "ammo": "ammo"}[kind], shooter="Alice")
        for _ in range(shots):
            sim.fire()
        sim.deliver()
        return "localStorage.setItem('fet_cid','layout-client-0001');localStorage.setItem('fet_name','Alice');"

    def test_the_lane_on_a_phone_and_a_tablet(self):
        init = self.lane()
        script = r"""
for (let i = 0; i < 80 && !document.querySelector('.face'); i++) await sleep(50);
const f = document.querySelector('.facebox'), fr = f ? f.getBoundingClientRect() : null, bar = document.querySelector('.lanebar');
const br = bar ? bar.getBoundingClientRect() : null;
return {overflow: overflow(), small: small('.lane button, .lane a, .shoot .right button, .shoot .right a, .zseg button'),
        square: fr ? Math.abs(fr.width - fr.height) <= 2 : null, faceW: fr ? Math.round(fr.width) : 0,
        bar: br ? Math.round(innerHeight - br.bottom) : null, phone: !!document.querySelector('.phone.lane'), wide: !!document.querySelector('.shoot'),
        rangemode: document.body.classList.contains('rangemode'), grid: document.querySelectorAll('.shotgrid .sg').length};"""
        for size in (PHONE, TABLET):
            for scheme in ("light", "dark"):
                with self.subTest(size=size, scheme=scheme):
                    v = self.look("/#/t/lane-1", size, scheme, script, init)
                    self.assertFalse(v["overflow"], "no sideways scrolling")
                    self.assertEqual(v["small"], [], "every control at least 44 px")
                    self.assertTrue(v["square"], "the face is square")
                    self.assertTrue(v["rangemode"], "holding the lane: only its screen")
                    self.assertGreaterEqual(v["grid"], 8)
                    if size == PHONE:
                        self.assertTrue(v["phone"])
                        self.assertGreaterEqual(v["faceW"], 340, "the face as wide as the phone")
                        self.assertEqual(v["bar"], 0, "the actions sit at the bottom of the screen")
                    else:
                        self.assertTrue(v["wide"])
                        self.assertGreaterEqual(v["faceW"], 600, "a large face on a tablet")

    def range_of(self, n):
        """n lanes with a named shooter and shots each, as the spectators see them."""
        from fetserver.simtarget import SimTarget
        self.sims = []
        for i in range(1, n + 1):
            sim = SimTarget(self.url, "test-token", "lane-%d" % i, "Lane %d" % i, seed=i)
            self.sims.append(sim)
            sim.heartbeat()
            self.core.set_lane("lane-%d" % i, i)
            self.core.acquire_control("lane-%d" % i, "shooter-client-%04d" % i, "S%d" % i)
            self.core.set_mode("lane-%d" % i, "shooter-client-%04d" % i, "practice", shooter="Shooter number %d" % i)
            for _ in range(6):
                sim.fire()
            sim.deliver()

    def match_range(self, n=6):
        """A running match on n lanes with shots (the simulator fires close together: flagged shots), and a match
        director logged in: (match id, the session cookie)."""
        self.range_of(n)
        m = self.core.matches
        m.set_user("md", "director", "layout-pass-1")
        fid = m.save_format({"name": "Club 20", "book": "ISSF", "scoring": "decimal", "phases": [
            {"kind": "sight", "label": "Preparation and sighting", "seconds": 600},
            {"kind": "record", "label": "Match", "seconds": 3000, "shots": 20}]}, "md")["id"]
        names = ["Maya Chen", "J. Ortiz", "Sam Price", "Riley K.", "A. Novak", "Theo Banks", "P. Iyer", "Lee Park"]
        mid = m.create("Tuesday League", fid, [{"device_id": "lane-%d" % (i + 1), "competitor": names[i % 8]} for i in range(n)], "md")
        m.start(mid, "md")
        m.command(mid, "next", "md")
        for i, sim in enumerate(self.sims):
            for _ in range(3 + i % 3):
                sim.fire()
            sim.deliver()
        req = urllib.request.Request(self.url + "/api/v1/match/login", data=json.dumps({"username": "md", "password": "layout-pass-1"}).encode(),
                                     headers={"Content-Type": "application/json", "X-FET-Admin": "1"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            cookie = r.headers["Set-Cookie"].split(";")[0]
        return mid, cookie

    CONSOLE = r"""
for (let i = 0; i < 80 && !document.querySelector('[data-act=mcopen]'); i++) await sleep(50);
document.querySelector('[data-act=mcopen]').click();
for (let i = 0; i < 80 && !document.querySelector('[data-clock=console]'); i++) await sleep(50);
await sleep(400);
const top = el => el ? Math.round(el.getBoundingClientRect().top + scrollY) : null;
"""

    def test_the_range_officers_console_on_a_phone_and_a_tablet(self):
        mid, cookie = self.match_range()
        for size in (PHONE, TABLET):
            for scheme in ("light", "dark"):
                with self.subTest(size=size, scheme=scheme):
                    v = self.look("/#/match/singles", size, scheme, self.CONSOLE + r"""
return {overflow: overflow(), small: small('.roconsole button, .roconsole a:not(.rotitle a), .roconsole summary'), lanes: top(document.querySelector('.rolane')),
        acts: top(document.querySelector('.roacts')), rest: top(document.querySelector('.rorest')), left: Math.round(document.querySelector('.rolane').getBoundingClientRect().left),
        tabs: !!document.querySelector('.mc .tabs, .mc .secsel'), clock: document.querySelector('[data-clock=console]').textContent};""", cookie=cookie)
                    self.assertFalse(v["overflow"], "no sideways scrolling")
                    self.assertEqual(v["small"], [], "every control at least 44 px")
                    self.assertFalse(v["tabs"], "the console alone (its back button returns)")
                    self.assertRegex(v["clock"], r"^\d+:\d\d$")
                    if size == PHONE:
                        self.assertLess(v["acts"], v["lanes"], "the lanes right under the quick actions")
                        self.assertLess(v["lanes"], v["rest"], "the rest after the lanes")
                    else:
                        self.assertGreater(v["left"], 500, "the lanes in their own column")

    def test_deciding_a_flagged_shot_from_the_sheet(self):
        mid, cookie = self.match_range(2)
        flags = [f for f in self.core.matches.detail(mid)["flags"] if not f["decision"]]
        self.assertTrue(flags, "the simulator's close shots are flagged")
        v = self.look("/#/match/singles", PHONE, "light", self.CONSOLE + r"""
const f = document.querySelector('[data-act=mcflag]'), id = +f.dataset.id;
f.click(); await sleep(300);
const sheet = document.querySelector('#overlay .sheet'), save = () => sheet.querySelector('[data-act=mcflagsave]');
const before = save().disabled;
sheet.querySelector('input[data-flagpick][value=count]').click(); await sleep(200);
const after = document.querySelector('#overlay [data-act=mcflagsave]').disabled;
document.querySelector('#overlay [data-act=mcflagsave]').click();
for (let i = 0; i < 60 && document.getElementById('overlay').classList.contains('open'); i++) await sleep(50);
await sleep(300);
return {id, before, after, closed: !document.getElementById('overlay').classList.contains('open'), small: small('#overlay button, #overlay label'), overflow: overflow()};""", cookie=cookie)
        self.assertEqual((v["before"], v["after"], v["closed"]), (True, False, True), "Save waits for a choice, saves, the sheet closes")
        d = {f["shot_id"]: f["decision"] for f in self.core.matches.detail(mid)["flags"]}
        self.assertEqual(d[v["id"]], "count", "the server has the decision")

    def director(self):
        self.core.matches.set_user("md", "director", "layout-pass-1")
        req = urllib.request.Request(self.url + "/api/v1/match/login", data=json.dumps({"username": "md", "password": "layout-pass-1"}).encode(),
                                     headers={"Content-Type": "application/json", "X-FET-Admin": "1"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.headers["Set-Cookie"].split(";")[0]

    def range_manager(self):
        """Range Control logged in: the session cookie."""
        from fetserver.admin import write_password
        write_password(Path(self.tmp.name), "range-pass-1")
        req = urllib.request.Request(self.url + "/api/v1/admin/login", data=json.dumps({"password": "range-pass-1"}).encode(),
                                     headers={"Content-Type": "application/json", "X-FET-Admin": "1"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.headers["Set-Cookie"].split(";")[0]

    def test_a_new_match_made_on_a_phone_and_a_tablet(self):
        self.range_of(5)
        cookie = self.director()
        script = r"""
for (let i = 0; i < 80 && !document.querySelector('[data-act=mcnew]'); i++) await sleep(50);
document.querySelector('[data-act=mcnew]').click(); await sleep(400);
const sel = document.querySelector('#mcfmt'), opt = [...sel.options].find(o => /10m Air Rifle 60/.test(o.textContent));
sel.value = opt.value; sel.dispatchEvent(new Event('input', {bubbles: true})); await sleep(400);
const name = document.querySelector('#mcname'); name.value = 'Phone night'; name.dispatchEvent(new Event('input', {bubbles: true}));
document.querySelector('[data-mcl="lane-A"]').click(); document.querySelector('[data-mcl="lane-B"]').click();
const c = document.querySelector('[data-mcc="lane-B"]'); c.value = 'Ann <b>Lee'; c.dispatchEvent(new Event('input', {bubbles: true}));
const t = document.querySelector('[data-mct="3"]'); t.value = '60'; t.dispatchEvent(new Event('input', {bubbles: true}));
await sleep(100);
const out = {overflow: overflow(), small: small('.nm button, .nm input:not([type=checkbox]), .nm select, .nm label'), steps: document.querySelectorAll('.howrun li').length,
             cols: new Set([...document.querySelectorAll('.nmcol')].map(e => Math.round(e.getBoundingClientRect().left))).size};
document.querySelector('[data-act=mccreate]').click();
for (let i = 0; i < 80 && !document.querySelector('[data-clock=console]'); i++) await sleep(50);
out.console = !!document.querySelector('[data-clock=console]');
return out;"""
        for size, a, b in ((PHONE, 2, 3), (TABLET, 4, 5)):
            with self.subTest(size=size):
                v = self.look("/#/match/singles", size, "light", script.replace("lane-A", "lane-%d" % a).replace("lane-B", "lane-%d" % b), cookie=cookie)
                self.assertFalse(v["overflow"])
                self.assertEqual(v["small"], [])
                self.assertEqual(v["steps"], 4, "how it will run: the format's phases")
                self.assertEqual(v["cols"], 1 if size == PHONE else 2)
                self.assertTrue(v["console"], "created: its console opens")
        ms = [m for m in self.core.matches.list() if m["name"] == "Phone night"]
        self.assertEqual(len(ms), 2)
        d = self.core.matches.detail(min(m["id"] for m in ms))
        self.assertEqual([(l["device_id"], l["competitor"]) for l in d["lanes"]], [("lane-2", None), ("lane-3", "Ann <b>Lee")])
        self.assertEqual(d["format"]["phases"][3]["seconds"], 3600, "a changed time is kept")
        self.assertTrue(d["format"].get("modified"), "and marked as modified")

    def test_match_control_pages_on_a_phone_and_a_tablet(self):
        mid, cookie = self.match_range(4)
        m, cc = self.core.matches, self.core.competitions
        fid = m.save_format({"name": "Club 20", "book": "ISSF", "scoring": "decimal", "phases": [{"kind": "record", "label": "Match", "seconds": 3000, "shots": 20}]}, "md")["id"]
        cid = cc.create("Riverside Open", fid, "md")
        for f, g in (("Taylor", "R."), ("Park", "S."), ("Novak", "E.")):
            cc.save_competitor(cid, {"family": f, "given": g, "class": "Open"})
        pages = {"/#/match": "", "/#/match/singles": "", "/#/match/results": "", "/#/match/formats": "",
                 "/#/match?comp": "document.querySelector('[data-act=mccopen]').click(); for (let i = 0; i < 80 && !document.querySelector('.comp .relays'); i++) await sleep(50);",
                 "/#/match/formats?new": "document.querySelector('[data-act=mcfnew]').click(); await sleep(300);"}
        for path, step in pages.items():
            for size in (PHONE, TABLET):
                with self.subTest(path=path, size=size):
                    v = self.look(path.split("?")[0], size, "light", "await sleep(800);" + step + r"""await sleep(300);
return {overflow: overflow(), small: small('.mc button, .mc a:not(.rotitle a):not(p a), .mc select, .mc input:not([type=checkbox]), .mc summary, .mc label.tick')};""", cookie=cookie)
                    self.assertFalse(v["overflow"], "no sideways scrolling")
                    self.assertEqual(v["small"], [], "every control at least 44 px")

    def test_your_own_match_phases_reordered_and_saved(self):
        self.range_of(1)
        cookie = self.director()
        v = self.look("/#/match/formats", PHONE, "light", r"""
await sleep(700); document.querySelector('[data-act=mcfnew]').click(); await sleep(300);
const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); };
set('[data-mcf=name]', 'Swap test'); set('[data-mcf=book]', 'ISSF');
while (document.querySelectorAll('.phcards li').length < 2) { document.querySelector('[data-act=mcpadd]').click(); await sleep(100); }
set('[data-mcp="0:kind"]', 'record'); set('[data-mcp="0:shots"]', '10'); set('[data-mcp="0:label"]', 'First'); set('[data-mcp="1:kind"]', 'sight'); set('[data-mcp="1:label"]', 'Second');
await sleep(100);
document.querySelector('[data-act=mcpmove][data-i="1"][data-d="-1"]').click(); await sleep(200);
const labels = [...document.querySelectorAll('[data-mcp$=":label"]')].map(e => e.value);
document.querySelector('[data-act=mcfsave]').click(); await sleep(800);
return {labels, overflow: overflow(), msg: (document.querySelector('.fmted .bad') || {}).textContent || ''};""", cookie=cookie)
        self.assertEqual(v["msg"], "")
        self.assertEqual(v["labels"][:2], ["Second", "First"], "moved up")
        mine = [f for f in self.core.matches.formats() if f["name"] == "Swap test"]
        self.assertEqual(len(mine), 1, "saved")
        self.assertEqual([p["label"] for p in mine[0]["phases"]][:2], ["Second", "First"], "in the new order")

    RC_SMALL = r"""return {overflow: overflow(), small: small('.rc button, .rc a:not(p a):not(.fact a), .rc select, .rc input:not([type=checkbox]):not([type=radio]), .rc summary, #overlay .sheet button, #overlay .sheet select, .about a.tlink, .signin button, .signin input')};"""

    def test_range_control_on_a_phone_and_a_tablet(self):
        self.range_of(3)
        cookie = self.range_manager()
        pages = [("/#/range/" + t, "") for t in ("targets", "firmware", "flash", "tv", "pi", "users")] + [
            ("/#/range", "document.querySelector('[data-act=rctopen]').click(); await sleep(400);"), ("/#/about", "")] + [
            ("/#/range/flash", "document.querySelector('[data-act=rcwiz][data-k=%s]').click(); await sleep(300);" % k +     # the flash station step by step
             "for (let i = 0; i < %d; i++) { document.querySelector('[data-act=rcwizstep][data-d=\"1\"]').click(); await sleep(250); }" % n)
            for k, n in (("mega", 4), ("esp32", 2), ("esp32", 3))]
        for path, step in pages:
            for size in (PHONE, TABLET):
                with self.subTest(path=path, step=bool(step), size=size):
                    v = self.look(path, size, "dark" if size == TABLET else "light", "await sleep(1000);" + step + self.RC_SMALL, cookie=cookie)
                    self.assertFalse(v["overflow"], "no sideways scrolling")
                    self.assertEqual(v["small"], [], "every control at least 44 px")
        for path in ("/#/range", "/#/match"):
            with self.subTest(signin=path):
                v = self.look(path, PHONE, "light", "await sleep(800);" + self.RC_SMALL)
                self.assertFalse(v["overflow"])
                self.assertEqual(v["small"], [])

    def test_one_target_changed_from_its_sheet(self):
        self.range_of(2)
        cookie = self.range_manager()
        v = self.look("/#/range", PHONE, "light", r"""
await sleep(1000);
document.querySelector('[data-act=rctopen][data-id="lane-2"]').click(); await sleep(400);
const sheet = document.querySelector('#overlay .sheet');
document.querySelector('#overlay [data-act=rcmode][data-mode=ammo]').click(); await sleep(800);
return {open: !!sheet, title: sheet.querySelector('h2').textContent};""", cookie=cookie)
        self.assertIn("Lane 2", v["title"])
        modes = {t["device_id"]: t["mode"] for t in self.core.list_targets(None, staff=True)}
        self.assertEqual((modes["lane-2"], modes["lane-1"]), ("ammo", "practice"), "only that target changed")

    def test_the_tv_board_fits_one_screen(self):
        mid, cookie = self.match_range(12)
        for f in self.core.matches.detail(mid)["flags"]:
            self.core.matches.decide(mid, f["shot_id"], "count", "md")
        v = self.look("/#/tv/board", TV, "light", r"""
for (let i = 0; i < 80 && !document.querySelector('.board tr td'); i++) await sleep(50);
await sleep(300);
return {overflow: overflow(), h: document.documentElement.scrollHeight, rows: document.querySelectorAll('.board tr td.pl').length, size: document.querySelector('.board').className,
        faces: document.querySelectorAll('.tvface').length, theme: document.documentElement.dataset.theme, bg: getComputedStyle(document.body).backgroundColor,
        buttons: document.querySelectorAll('.board button, .board [data-act], .board a[href]').length};""")
        self.assertEqual(v["rows"], 12)
        self.assertIn("tv-small", v["size"])
        self.assertFalse(v["overflow"])
        self.assertLessEqual(v["h"], 1080, "twelve lanes on one TV screen")
        self.assertEqual(v["faces"], 4, "the leaders' targets beside it")
        self.assertEqual((v["theme"], v["bg"]), ("dark", "rgb(14, 22, 30)"), "a TV is dark")
        self.assertEqual(v["buttons"], 0, "read only")
        t = self.look("/#/tv/targets?m=%d" % mid, TV, "light", "for (let i = 0; i < 80 && !document.querySelector('.card'); i++) await sleep(50); return {overflow: overflow(), cards: document.querySelectorAll('.card').length};")
        self.assertEqual((t["overflow"], t["cards"]), (False, 12))

    def test_a_final_with_every_shot_fits_one_tv_screen(self):
        """The worst case: eight finalists, all 24 shots each (two series and 14 single shots), a shoot-off column, the targets strip with all eight (nobody out yet). The page's
        /board answer is replaced by that final (the server's own finals are driven in tests/test_finals*.py)."""
        self.range_of(8)
        rows = [{"place": i + 1, "tied": False, "pos": "ABCDEFGH"[i], "name": "FINALIST NUMBER %d" % (i + 1), "lane": i + 1, "device_id": "lane-%d" % (i + 1),
                 "qual": 630.0, "final": 240.0, "total": 240.0, "shots": 24, "shoot_off": [10.1] if i in (1, 2) else [], "dq": None,
                 "out": None, "last": 10.9, "series": [{"v": 104.9, "n": 5, "of": 5}] * 2 + [{"v": 10.0 + k / 10, "n": 1, "of": 1} for k in range(14)]} for i in range(8)]
        board = {"matches": [{"id": 9, "name": "League - final", "format_name": "ISSF 10m Air Rifle Final", "state": "running", "scoring": "decimal",
                              "clock": {"label": "Shoot-off 1", "remaining_ms": 40000, "phase": 40, "kind": "record"},
                              "final": {"title": "ISSF 10m Air Rifle Final (ISSF 6.17.2)", "elim": True, "shots_done": 24, "shots_total": 24, "now": "Shoot-off 1",
                                        "shoot_off": [2, 3], "next_out": None, "rows": rows}}],
                 "competitions": [], "server_time_ms": 1800000000000}
        init = "(() => { const f = window.fetch; window.fetch = (u, o) => /\\/api\\/v1\\/board/.test(String(u)) ? " \
               "Promise.resolve(new Response(%s, {headers: {'Content-Type': 'application/json'}})) : f(u, o); })();" % json.dumps(json.dumps(board))
        v = self.look("/#/tv/board", TV, "dark", r"""
for (let i = 0; i < 80 && !document.querySelector('.tvfinal td.fshots'); i++) await sleep(50);
await sleep(300);
const lines = [...document.querySelectorAll('.tvfinal td.fshots')].map(td => { const t = [...td.querySelectorAll('span')].map(s => s.getBoundingClientRect().top); return Math.max(...t) - Math.min(...t) > 12 ? 2 : 1; });
return {overflow: overflow(), h: document.documentElement.scrollHeight, shots: document.querySelectorAll('.tvfinal td.fshots span').length,
        rows: document.querySelectorAll('.tvfinal td.fshots').length, wrapped: lines.filter(n => n > 1).length, faces: document.querySelectorAll('.tvface').length,
        font: parseFloat(getComputedStyle(document.querySelector('.tvfinal td.fshots span')).fontSize)};
""", init=init)
        self.assertEqual((v["rows"], v["shots"]), (8, 128), "every finalist: two series totals and 14 single shots")
        self.assertEqual(v["faces"], 8, "the finalists' targets below")
        self.assertGreaterEqual(v["font"], 20, "readable from the seats")
        self.assertFalse(v["overflow"])
        self.assertLessEqual(v["h"], 1080, "a full final on one TV screen")
        self.assertEqual(v["wrapped"], 0, "each finalist's shots on one line")

    def test_a_saved_tv_display_is_dark_unless_it_chooses_light(self):
        self.range_of(2)
        auto = self.core.displays.save({"name": "Hall", "show": "board", "follow": "range"})
        light = self.core.displays.save({"name": "Lobby", "show": "board", "follow": "range", "theme": "light"})
        for code, want in ((auto, "dark"), (light, "light")):
            with self.subTest(code=code):
                v = self.look("/" + code, TV, "dark" if want == "light" else "light", "await sleep(1200); return {theme: document.documentElement.dataset.theme, overflow: overflow()};")
                self.assertEqual((v["theme"], v["overflow"]), (want, False))

    def test_all_lanes_on_a_tablet(self):
        self.range_of(6)
        v = self.look("/#/lanes", TABLET, "dark", r"""
for (let i = 0; i < 80 && !document.querySelector('.lcard'); i++) await sleep(50);
const cards = [...document.querySelectorAll('.lcard')];
return {n: cards.length, cols: new Set(cards.map(c => Math.round(c.getBoundingClientRect().left))).size, overflow: overflow(),
        fits: cards[cards.length - 1].getBoundingClientRect().bottom <= innerHeight};""")
        self.assertEqual((v["n"], v["cols"], v["overflow"]), (6, 3, False))
        self.assertTrue(v["fits"], "six lanes on one tablet screen")

    def test_watching_lanes_on_a_phone_and_a_tablet(self):
        self.range_of(12)
        script = r"""
for (let i = 0; i < 80 && !document.querySelector('.wgrid, .wempty'); i++) await sleep(50);
await sleep(100);
const cards = Array.from(document.querySelectorAll('.wcard')), last = cards[cards.length - 1];
return {overflow: overflow(), small: small('.sptabs a, .whead a, .worder button, .wcard'), n: cards.length,
        cls: (document.querySelector('.wgrid') || {}).className, cols: new Set(cards.map(c => Math.round(c.getBoundingClientRect().left))).size,
        fits: last ? last.getBoundingClientRect().bottom <= innerHeight : null, team: document.querySelectorAll('.wcard.team').length};"""
        for n, cls in ((3, "w-panels"), (8, "w-tiles"), (12, "w-dense")):
            picks = json.dumps({"picks": [{"lane": i, "name": "Shooter number %d" % i} for i in range(1, n + 1)], "team": ["Shooter number 2"], "order": "lane"})
            init = "localStorage.setItem('fet_watch', %s);" % json.dumps(picks)
            for size in (PHONE, TABLET):
                for scheme in ("light", "dark"):
                    with self.subTest(n=n, size=size, scheme=scheme):
                        v = self.look("/#/watch", size, scheme, script, init)
                        self.assertEqual(v["n"], n)
                        self.assertIn(cls, v["cls"])
                        self.assertFalse(v["overflow"], "no sideways scrolling")
                        self.assertEqual(v["small"], [], "every control at least 44 px")
                        self.assertEqual(v["team"], 1)
                        if size == PHONE:
                            self.assertEqual(v["cols"], {"w-panels": 1, "w-tiles": 2, "w-dense": 3}[cls])
                            if cls == "w-tiles":
                                self.assertTrue(v["fits"], "5-8 lanes on a phone: no scrolling")
                        else:
                            self.assertTrue(v["fits"], "a tablet shows even 12 lanes without scrolling")

    def test_choosing_lanes_and_the_ranking_and_results_tabs(self):
        self.range_of(4)
        pick = self.look("/#/watch/pick", PHONE, "light", r"""
for (let i = 0; i < 80 && !document.querySelector('.wpick'); i++) await sleep(50);
document.querySelector('.wpick input[data-dev="lane-3"]').click(); await sleep(50);
document.querySelector('.wpick button[data-name="Shooter number 3"]').click(); await sleep(50);
const done = document.querySelector('.wdone a').getBoundingClientRect();
return {rows: document.querySelectorAll('.wpick').length, small: small('.wpick label, .wpick button, .wdone a'), overflow: overflow(),
        stored: JSON.parse(localStorage.getItem('fet_watch')), done: Math.round(innerHeight - done.bottom), label: document.querySelector('.wdone a').textContent};""")
        self.assertEqual(pick["rows"], 4)
        self.assertEqual(pick["small"], [])
        self.assertFalse(pick["overflow"])
        self.assertEqual(pick["stored"], {"picks": [{"lane": 3, "name": "Shooter number 3"}], "team": ["Shooter number 3"], "order": "lane"})
        self.assertEqual(pick["label"], "Watch 1 lane")
        self.assertGreaterEqual(pick["done"], 0)
        for path in ("/#/", "/#/lanes", "/#/results"):
            for scheme in ("light", "dark"):
                with self.subTest(path=path, scheme=scheme):
                    v = self.look(path, PHONE, scheme, r"""
for (let i = 0; i < 80 && !document.querySelector('.sptabs'); i++) await sleep(50);
return {tabs: Array.from(document.querySelectorAll('.sptabs a')).map(a => a.textContent), on: (document.querySelector('.sptabs a.on') || {}).textContent,
        small: small('.sptabs a, .zseg button, .rrow, .rlist'), overflow: overflow()};""")
                    self.assertEqual(v["tabs"], ["Ranking", "Lanes", "Watching", "Results"])
                    self.assertEqual(v["on"], {"/#/": "Lanes", "/#/lanes": "Lanes", "/#/results": "Results"}[path], "no match on: home is the lanes")
                    self.assertEqual(v["small"], [])
                    self.assertFalse(v["overflow"])

    def test_fonts_are_served_only_by_name(self):
        for name in ("archivo-latin", "archivo-latin-ext", "jetbrains-mono-latin", "jetbrains-mono-latin-ext"):
            with urllib.request.urlopen(self.url + "/fonts/%s.woff2" % name, timeout=5) as r:
                self.assertEqual((r.status, r.headers["Content-Type"]), (200, "font/woff2"))
                self.assertEqual(r.read()[:4], b"wOF2")
        for bad in ("/fonts/OFL-Archivo.txt", "/fonts/../server.py", "/fonts/x.woff2"):
            with self.subTest(bad=bad):
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(self.url + bad, timeout=5)


# ---------------------------------------------------------------------------------------------------- contrast
def tokens(block: str) -> dict:
    return dict(re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6})", block))


def lum(hexc: str) -> float:
    def ch(v):
        v = int(v, 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = ch(hexc[1:3]), ch(hexc[3:5]), ch(hexc[5:7])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


PAIRS = [("fg", "bg"), ("fg", "card"), ("mut", "bg"), ("mut", "card"), ("link", "card"), ("on-accent", "accent"), ("bad", "card"),
         ("ok", "card"), ("warn", "card"), ("last-ink", "last-bg"), ("last-ink", "card"), ("fg", "hi"), ("mut", "hi"),
         ("bf-practice", "b-practice"), ("bf-sighter", "b-sighter"), ("bf-record", "b-record"), ("bf-ammo", "b-ammo"),
         ("band-ink", "band"), ("band-mut", "band"), ("warn-ink", "warn-bg"), ("fg", "surface2"), ("mut", "surface2"), ("fg", "warn-bg"), ("last-ink", "bg"), ("warn-ink", "bg")]


class Contrast(unittest.TestCase):
    def test_every_text_pair_light_and_dark(self):
        light = tokens(re.search(r"\n:root\{(color-scheme:light;.*?)\}", PAGE).group(1))
        dark = tokens(re.search(r"\n:root\[data-theme=dark\]\{(.*?)\}", PAGE).group(1))
        self.assertEqual(dark, tokens(re.search(r":root:not\(\[data-theme=light\]\)\{(.*?)\}\}", PAGE).group(1)),
                         "the system's dark and the chosen dark are the same")
        for name, t in (("light", light), ("dark", dark)):
            for fg, bg in PAIRS:
                with self.subTest(theme=name, pair=(fg, bg)):
                    self.assertGreaterEqual(ratio(t[fg], t[bg]), 4.5, "%s on %s: %.2f" % (fg, bg, ratio(t[fg], t[bg])))

    def test_the_ratio_itself(self):
        self.assertAlmostEqual(ratio("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(ratio("#777777", "#FFFFFF"), 4.48, places=2)


if __name__ == "__main__":
    unittest.main()
