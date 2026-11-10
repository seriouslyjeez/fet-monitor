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
    def look(self, path, size, scheme, script, init=None):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(LIB + script)
        self.addCleanup(os.unlink, f.name)
        env = dict(os.environ, CDP_INIT=init) if init else None
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
                self.assertEqual(v["items"], ["Lanes", "Results", "Match Control", "Range Control"])
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
         ("bf-practice", "b-practice"), ("bf-sighter", "b-sighter"), ("bf-record", "b-record"), ("bf-ammo", "b-ammo")]


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
