"""fetcore on MicroPython (the CYD display, docs/PLAN-cyd.md): the parts that make a shot's position and score give
the same results as on CPython, to the bit. Needs a `micropython` with double-precision floats on PATH (macOS:
brew install micropython); skipped without one. The same check on the CYD itself: docs/PLAN-cyd.md.

Found by these checks (2026-11-13): MicroPython reads some long decimals one ulp off, in source code too (5 of fdlibm's
constants: fmath now gives them as bits); its dicts keep no order (targetproto checked the field order with one); its
re has no fullmatch, counts or lookahead; it has no decimal, dataclasses or math.hypot."""
import ast
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MICROPYTHON = shutil.which("micropython")
PORTABLE = ("exact", "faces", "fmath", "lineparse", "scoring", "shotsolve", "targetproto")   # what the CYD runs


def run(cmd, *args):
    return subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


@unittest.skipUnless(MICROPYTHON, "micropython not installed (brew install micropython)")
class OnMicroPython(unittest.TestCase):
    def test_same_output_as_cpython(self):
        script = str(ROOT / "tests" / "mp_conformance.py")
        cp = run(["python3"], script, str(ROOT))
        mp = run([MICROPYTHON, "-X", "heapsize=64m"], script, str(ROOT))
        self.assertEqual(cp.returncode, 0, cp.stderr[-2000:])
        self.assertEqual(mp.returncode, 0, (mp.stdout + mp.stderr)[-2000:])
        self.assertIn("values 6", cp.stdout)
        self.assertIn("mismatches 0", cp.stdout)
        a, b = cp.stdout.splitlines(), mp.stdout.splitlines()
        first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
        self.assertEqual(len(a), len(b), "line counts differ")
        self.assertIsNone(first, None if first is None else "line %d:\nCPython     %s\nMicroPython %s" % (first + 1, a[first][:300], b[first][:300]))

    def test_every_float_literal_reads_the_same(self):
        """A literal MicroPython reads differently changes results silently (it did for 5 of fdlibm's constants)."""
        rows = []
        for f in sorted((ROOT / "fetcore").glob("*.py")):
            src = f.read_text()
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    rows.append("%s %d %s %s" % (f.name, node.lineno, ast.get_source_segment(src, node),
                                                 struct.pack("<d", node.value).hex()))
        self.assertGreater(len(rows), 100)
        with tempfile.TemporaryDirectory() as tmp:
            lits = os.path.join(tmp, "literals.txt")
            Path(lits).write_text("\n".join(rows) + "\n")
            check = os.path.join(tmp, "check.py")
            Path(check).write_text(
                "import struct, sys\n"
                "for line in open(sys.argv[1]):\n"
                "    f, ln, text, bits = line.split()\n"
                "    if struct.pack('<d', eval(text)).hex() != bits:\n"
                "        print(f, ln, text)\n")
            mp = run([MICROPYTHON], check, lits)
        self.assertEqual(mp.returncode, 0, mp.stderr)
        self.assertEqual(mp.stdout.strip(), "", "float literals MicroPython reads differently (write them as bits):\n" + mp.stdout)

    def test_the_portable_modules_import(self):
        code = "import sys\nsys.path.insert(0, %r)\nsys.path.insert(1, %r)\n" % (str(ROOT), str(ROOT / "firmware" / "display" / "lib"))
        code += "".join("import fetcore.%s\n" % m for m in PORTABLE) + "print('ok')\n"
        mp = run([MICROPYTHON, "-c", code])
        self.assertEqual(mp.stdout.strip(), "ok", mp.stderr)


if __name__ == "__main__":
    unittest.main()
