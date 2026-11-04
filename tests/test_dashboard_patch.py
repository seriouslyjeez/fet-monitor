"""The dashboard's in-place redraw (patchHtml in fetserver/web/dashboard.html), in a real browser: headless Chrome
runs the page's own functions against a set of cases and writes the results into the page, which is read back.
Skipped when no Chrome is installed."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium", "chromium-browser"]


def chrome():
    for c in CHROMES:
        p = c if os.path.isabs(c) and os.path.exists(c) else shutil.which(c)
        if p:
            return p
    return None


CASES = r"""
const out = {};
const app = document.getElementById('app');
const draw = h => patchHtml(app, h);
// 1. the same elements are kept, only the text changes
draw('<div class="panel"><p id="a">5 s ago</p><button data-act="x">Go</button></div>');
const p0 = app.querySelector('#a'), b0 = app.querySelector('button');
draw('<div class="panel"><p id="a">6 s ago</p><button data-act="x">Go</button></div>');
out.kept = app.querySelector('#a') === p0 && app.querySelector('button') === b0;
out.text = p0.textContent;
// 2. the field being typed in keeps what is typed; another field shows what the page says
draw('<input id="f1" value="1"><input id="f2" value="2">');
const f1 = app.querySelector('#f1'), f2 = app.querySelector('#f2');
f1.focus(); f1.value = 'typed'; f2.value = 'stale';
draw('<input id="f1" value="9"><input id="f2" value="8">');
out.focused = f1.value; out.other = f2.value; f1.blur();
// 3. a section the user opened stays open
draw('<details id="d"><summary>More</summary><p>x</p></details>');
app.querySelector('#d').open = true;
draw('<details id="d"><summary>More</summary><p>y</p></details>');
out.open = app.querySelector('#d').open;
// 4. a choice the page says differently is put back; checkboxes follow too
draw('<select id="s"><option value="0" selected>No</option><option value="1">Yes</option></select><input type="checkbox" id="c">');
app.querySelector('#s').value = '1'; app.querySelector('#c').checked = true;
draw('<select id="s"><option value="0" selected>No</option><option value="1">Yes</option></select><input type="checkbox" id="c">');
out.select = app.querySelector('#s').value; out.check = app.querySelector('#c').checked;
draw('<select id="s"><option value="0">No</option><option value="1" selected>Yes</option></select><input type="checkbox" id="c" checked>');
out.select2 = app.querySelector('#s').value; out.check2 = app.querySelector('#c').checked;
// 5. other elements are replaced, extra ones removed, new ones added
draw('<p>a</p><p>b</p><p>c</p>');
draw('<div>a</div><p>b</p>');
out.shape = Array.from(app.childNodes).map(n => n.nodeName + ':' + n.textContent).join(',');
draw('<p id="k1">1</p>');
const k1 = app.firstChild;
draw('<p id="k2">2</p>');
out.ids = app.innerHTML + (app.firstChild === k1 ? ' reused' : ' new');      // another id: another element, never reused
// 6. SVG stays SVG; its attributes change in place
draw('<svg viewBox="-26 -26 52 52"><circle cx="1" cy="2" r="3"></circle></svg>');
const circ = app.querySelector('circle');
draw('<svg viewBox="-9 -9 18 18"><circle cx="4" cy="5" r="3"></circle><circle cx="0" cy="0" r="1"></circle></svg>');
out.svg = app.querySelector('circle') === circ && circ.getAttribute('cx') === '4' && app.querySelector('svg').getAttribute('viewBox') === '-9 -9 18 18' &&
  app.querySelectorAll('circle')[1] instanceof SVGCircleElement;
// 7. a text area not being typed in shows the page's text; attributes the page dropped are removed
draw('<textarea id="t">one</textarea><b class="x" title="h">z</b>');
app.querySelector('#t').value = 'edited';
draw('<textarea id="t">two</textarea><b>z</b>');
out.textarea = app.querySelector('#t').value; out.attrs = app.querySelector('b').attributes.length;
// 8. the same markup twice gives the same page as a fresh copy would
const html = '<div class="row"><span class="title">A</span> <i>b</i><table><tr><td>1</td></tr></table></div>';
draw('<p>x</p>'); draw(html);
const fresh = document.createElement('div'); fresh.innerHTML = html;
out.same = app.innerHTML === fresh.innerHTML;
document.getElementById('out').textContent = JSON.stringify(out);
"""


@unittest.skipIf(chrome() is None, "no Chrome or Chromium here")
class PatchInChrome(unittest.TestCase):
    def test_redraw_in_place(self):
        page = (ROOT / "fetserver/web/dashboard.html").read_text()
        code = re.search(r"(function patchHtml\(.*?)\nlet pressing", page, re.S).group(1)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "patch.html"
            f.write_text("<!doctype html><meta charset=utf-8><div id=app></div><pre id=out></pre><script>" + code + CASES + "</script>")
            r = subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + d + "/prof",
                                "--dump-dom", f.as_uri()], capture_output=True, text=True, timeout=90)
        m = re.search(r'<pre id="out">(.*?)</pre>', r.stdout, re.S)
        self.assertIsNotNone(m, r.stdout[-1500:] + r.stderr[-1500:])
        out = json.loads(m.group(1).replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
        self.assertEqual(out, {
            "kept": True, "text": "6 s ago",
            "focused": "typed", "other": "8",
            "open": True,
            "select": "0", "check": False, "select2": "1", "check2": True,
            "shape": "DIV:a,P:b", "ids": '<p id="k2">2</p> new',
            "svg": True, "textarea": "two", "attrs": 0, "same": True,
        })


if __name__ == "__main__":
    unittest.main()
