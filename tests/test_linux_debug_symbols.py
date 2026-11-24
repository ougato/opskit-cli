"""Verify native symbolication and preserve runnable payloads in Linux builds."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import linux_debug_symbols as symbols


@unittest.skipUnless(sys.platform == "linux" and all(shutil.which(tool) for tool in (
    "gcc", "objcopy", "nm", "addr2line",
)), "Linux binutils and gcc required")
class LinuxDebugSymbolsTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.symbols = self.root / "symbols"
        source = self.root / "native.c"
        source.write_text("int answer(void) { return 42; }\n", encoding="utf-8")
        self.library = self.dist / "libanswer.so"
        subprocess.run(["gcc", "-g", "-shared", "-fPIC", "-Wl,--build-id", str(source), "-o", str(self.library)],
                       check=True)
        source = self.root / "launcher.c"
        source.write_text(
            '#include <string.h>\nint answer(void);\nint main(int argc, char **argv) {\n'
            '  return argc == 2 && strcmp(argv[1], "--version") == 0 && answer() == 42 ? 0 : 1;\n}\n',
            encoding="utf-8",
        )
        self.launcher = self.dist / "opskit"
        subprocess.run(["gcc", "-g", str(source), f"-L{self.dist}", "-lanswer", "-Wl,-rpath,$ORIGIN",
                        "-o", str(self.launcher)], check=True)
        with self.launcher.open("ab") as file:
            file.write(b"frozen launcher archive bytes")

    def test_split_preserves_launcher_links_and_source_line(self):
        launcher = self.launcher.read_bytes()
        original = self.library.read_bytes()
        mode = self.library.stat().st_mode
        alias = self.dist / "answer-alias.so"
        alias.symlink_to(self.library.name)
        overlay = self.dist / "overlay.so"
        overlay.write_bytes(original + b"embedded payload")
        exported = subprocess.check_output(["nm", "-D", "--defined-only", str(self.library)], text=True)
        address = next(line.split()[0] for line in exported.splitlines() if line.endswith(" answer"))
        location = subprocess.check_output(["addr2line", "-e", str(self.library), address], text=True).strip()
        self.assertTrue(location.endswith("native.c:1"), location)

        [record] = symbols.split_debug_symbols(self.dist, self.symbols)
        sidecar = self.symbols / record["debug_path"]
        self.assertEqual(sidecar.parent.name + sidecar.stem, record["build_id"])
        self.assertGreater(record["removed_bytes"], 0)
        self.assertEqual(subprocess.check_output(["addr2line", "-e", str(sidecar), address], text=True).strip(),
                         location)
        self.assertNotEqual(self.library.read_bytes(), original)
        self.assertEqual(self.library.stat().st_mode, mode)
        self.assertEqual(alias.readlink(), Path(self.library.name))
        self.assertEqual(self.launcher.read_bytes(), launcher)
        self.assertEqual(overlay.read_bytes(), original + b"embedded payload")
        report = json.loads((self.symbols / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["files"], [record])
        self.assertEqual(report["skipped"], [{"path": "overlay.so", "reason": "overlay"}])
        subprocess.run([str(self.launcher), "--version"], check=True)

    def test_publish_failure_restores_runtime_elf(self):
        original = self.library.read_bytes()
        with patch.object(symbols.os, "rename", side_effect=OSError("injected publication failure")):
            with self.assertRaisesRegex(OSError, "injected publication failure"):
                symbols.split_debug_symbols(self.dist, self.symbols)
        self.assertEqual(self.library.read_bytes(), original)
        self.assertFalse(self.symbols.exists())
        self.assertEqual(set(self.dist.iterdir()), {self.launcher, self.library})
