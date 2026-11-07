"""fetcore stays pure: the scoring brain FeT Monitor and deeptens.com share imports only the Python standard library
and itself. No database, no network, no FeT Monitor server code (docs/PLAN-deeptens.md, "One scoring brain")."""
import ast
import sys
import unittest
from pathlib import Path

FETCORE = Path(__file__).resolve().parent.parent / "fetcore"
STDLIB = set(getattr(sys, "stdlib_module_names", ())) or {
    "__future__", "math", "re", "json", "hmac", "hashlib", "typing", "dataclasses", "decimal", "functools", "itertools",
    "statistics", "collections", "enum", "fractions", "copy", "bisect", "string", "datetime", "time"}
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


if __name__ == "__main__":
    unittest.main()
