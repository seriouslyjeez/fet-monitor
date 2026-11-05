"""fetserver/esptool_shim/run.py: esptool 4.7.0 with the ESP32 stub flashers Debian's package leaves out
(stub_flasher_32s3.json etc.: FileNotFoundError on the Pi's flash station, 2026-11-04)."""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fetserver.esptool_shim import run as shim
from fetserver.flashstation import ESPTOOL_SHIM, FlashStation

SHIM_DIR = Path(shim.__file__).parent


class Resolve(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.debian = self.dir / "stub_flasher"
        self.debian.mkdir()

    def test_the_shipped_files_are_the_ones_named(self):
        self.assertEqual(set(shim.STUBS), {"stub_flasher_32.json", "stub_flasher_32s2.json", "stub_flasher_32s3.json"})
        for name, sha in shim.STUBS.items():
            self.assertEqual(hashlib.sha256((SHIM_DIR / name).read_bytes()).hexdigest(), sha, name)
        self.assertTrue((SHIM_DIR / "LICENSE").read_text().lstrip().startswith("GNU GENERAL PUBLIC LICENSE"))

    def test_only_what_esptool_lacks_for_its_own_version(self):
        p = str(self.debian / "stub_flasher_32s3.json")
        self.assertEqual(shim.resolve(p, "4.7.0"), str(SHIM_DIR / "stub_flasher_32s3.json"), "missing: ours")
        self.assertEqual(shim.resolve(p, "4.5.1"), p, "another esptool: its stub must match it, so not ours")
        own = self.debian / "stub_flasher_8266.json"
        own.write_text("{}")
        self.assertEqual(shim.resolve(str(own), "4.7.0"), str(own), "esptool's own file always wins")
        Path(p).write_text("{}")
        self.assertEqual(shim.resolve(p, "4.7.0"), p)
        other = str(self.debian / "stub_flasher_32c3.json")
        self.assertEqual(shim.resolve(other, "4.7.0"), other, "a chip we bring nothing for")

    def test_a_damaged_copy_is_not_used(self):
        mine = self.dir / "mine"
        mine.mkdir()
        (mine / "stub_flasher_32s3.json").write_text('{"entry": 0}')
        p = str(self.debian / "stub_flasher_32s3.json")
        self.assertEqual(shim.resolve(p, "4.7.0", here=str(mine)), p)
        (mine / "stub_flasher_32s3.json").unlink()
        self.assertEqual(shim.resolve(p, "4.7.0", here=str(mine)), p, "and none at all")


class Station(unittest.TestCase):
    def test_runs_esptool_through_the_shim_when_python_has_it(self):
        st = FlashStation(None, None, run=lambda *a: (127, ""), has_module=lambda name: name == "esptool")
        self.assertEqual(st._esptool(), [sys.executable, ESPTOOL_SHIM])
        self.assertTrue(Path(ESPTOOL_SHIM).is_file())

    def test_else_the_esptool_command(self):
        calls = []
        st = FlashStation(None, None, run=lambda argv, t, f: (calls.append(argv) or (0 if argv[0] == "esptool.py" else 127), "4.7.0"),
                          has_module=lambda name: False)
        self.assertEqual(st._esptool(), ["esptool.py"])


ESPTOOL = importlib.util.find_spec("esptool")


@unittest.skipUnless(ESPTOOL and ESPTOOL.submodule_search_locations, "esptool is not installed for this Python")
class LikeDebian(unittest.TestCase):
    """The real esptool, its Xtensa stubs taken away as in Debian's package: loading the ESP32-S3 stub fails without
    the shim and works with it."""

    def setUp(self):
        import esptool
        if esptool.__version__ != shim.FOR_VERSION:
            self.skipTest(f"esptool {esptool.__version__} here, the shim is for {shim.FOR_VERSION}")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        shutil.copytree(list(ESPTOOL.submodule_search_locations)[0], self.path / "esptool")
        for name in shim.STUBS:
            (self.path / "esptool" / "targets" / "stub_flasher" / name).unlink()

    def py(self, code):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(self.path), str(SHIM_DIR)]))
        return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)

    def test_load_the_stub(self):
        load = ("import esptool.loader as L; s = L.StubFlasher(L.get_stub_json_path('ESP32-S3')); "
                "print(L.__file__.startswith(%r), len(s.text), hex(s.entry))" % str(self.path))
        r = self.py(load)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No such file or directory", r.stderr)
        self.assertIn("stub_flasher_32s3.json", r.stderr, "the error seen on the Pi")
        r = self.py("import run; run.patch(); " + load)
        self.assertEqual(r.returncode, 0, r.stderr)
        ok, size, entry = r.stdout.split()
        self.assertEqual((ok, entry), ("True", "0x40378a50"), "this copy of esptool, the ESP32-S3 stub's entry point")
        self.assertGreater(int(size), 1000)

    def test_the_command_line(self):
        env = dict(os.environ, PYTHONPATH=str(self.path))
        r = subprocess.run([sys.executable, ESPTOOL_SHIM, "version"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (0, "4.7.0"), r.stderr)


if __name__ == "__main__":
    unittest.main()
