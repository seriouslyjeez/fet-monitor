"""FeT Display's QR codes (firmware/display/lib/qr.py) are the codes FeT Monitor's pages draw (fetserver/web/qrcode.js,
Kazuhiko Arase, MIT): the same modules for sign-in addresses and for random texts of every length up to the limit,
under CPython and under MicroPython. Skipped without Node (and the MicroPython half without `micropython`)."""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "firmware/display/lib"))
import qr  # noqa: E402

NODE = r"""
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const qrcode = new Function(code + '; return qrcode;')();
const texts = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = texts.map(t => { const q = qrcode(0, 'M'); q.addData(t); q.make(); const n = q.getModuleCount(), rows = [];
  for (let r = 0; r < n; r++) { let s = ''; for (let c = 0; c < n; c++) s += q.isDark(r, c) ? '1' : '0'; rows.push(s); } return rows.join('/'); });
process.stdout.write(JSON.stringify(out));
"""
MP_SCRIPT = r"""
import sys, json
sys.path.insert(0, %r)
import qr
for t in json.loads(open(%r).read()):
    print("/".join("".join(str(b) for b in row) for row in qr.encode(t)))
"""


def texts():
    rng = random.Random(20261114)
    out = ["https://deeptens.com/t/3/4/k7m2qx9a/", "https://deeptens.com:8443/t/12/17/ab23cd45/", "http://192.168.1.20:8080/#/t/fet-704131",
           "a", "A" * 14, "A" * 15, "x" * 26, "x" * 27, "y" * 42, "y" * 43, "z" * 62, "z" * 63, "w" * 84, "w" * 85,
           "v" * 106, "v" * 107, "u" * 122, "u" * 123, "t" * 152, "t" * 153, "s" * 180, "s" * 181, "r" * 213]
    for n in range(1, 214, 3):
        out.append("".join(chr(rng.randrange(32, 127)) for _ in range(n)))
    for _ in range(150):
        out.append("https://deeptens.com/t/%d/%d/%s/" % (rng.randrange(1, 9999), rng.randrange(1, 99),
                                                        "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))))
    return out


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class SameAsThePage(unittest.TestCase):
    def reference(self, ts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(NODE)
        self.addCleanup(os.unlink, f.name)
        r = subprocess.run(["node", f.name, str(ROOT / "fetserver/web/qrcode.js")], input=json.dumps(ts), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_cpython(self):
        ts = texts()
        ref = self.reference(ts)
        for t, want in zip(ts, ref):
            got = "/".join("".join(str(b) for b in row) for row in qr.encode(t))
            self.assertEqual(got, want, t)

    @unittest.skipUnless(shutil.which("micropython"), "micropython not installed")
    def test_micropython(self):
        ts = texts()[::4]
        ref = self.reference(ts)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "t.json").write_text(json.dumps(ts))
            Path(tmp, "run.py").write_text(MP_SCRIPT % (str(ROOT / "firmware/display/lib"), str(Path(tmp, "t.json"))))
            r = subprocess.run([shutil.which("micropython"), "-X", "heapsize=32m", str(Path(tmp, "run.py"))], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.stdout.splitlines(), ref, r.stderr[-500:])

    def test_limits(self):
        self.assertEqual(len(qr.encode("https://deeptens.com/t/3/4/k7m2qx9a/")), 29, "a sign-in address: version 3, 29 modules")
        for bad in ("x" * 214, "café"):
            with self.assertRaises(ValueError):
                qr.encode(bad)


if __name__ == "__main__":
    unittest.main()
