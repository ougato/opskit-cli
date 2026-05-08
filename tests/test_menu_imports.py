"""
严格检测所有 menu.py 的 import 完整性。

策略：
1. 通过 AST 解析每个 menu.py，找出所有 Name 引用
2. 对照 core.prompt 导出的公共符号，确认每个被调用的符号都在文件顶层 import 中
3. 任何缺失立即报错，防止 NameError 在运行时出现
"""
import ast
import importlib
import sys
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ─── core.prompt 所有公共可导入符号 ─────────────────────────────────────────────

PROMPT_SYMBOLS = {
    "select", "confirm", "text_input", "pause", "clear_screen",
    "UserCancel", "shield_ctrlc", "_read_key", "_kbhit",
}
# console 在各模块由 Console() 本地创建，不需要从 core.prompt 导入，排除在外


def _get_imported_names(filepath: Path) -> set[str]:
    """AST 解析文件，返回所有顶层 import 引入的名称集合"""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_as = alias.asname if alias.asname else alias.name
                names.add(imported_as)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_as = alias.asname if alias.asname else alias.name
                names.add(imported_as)
    return names


def _get_called_prompt_symbols(filepath: Path) -> set[str]:
    """AST 解析文件，返回所有被使用（Name 引用）的 core.prompt 符号"""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in PROMPT_SYMBOLS:
            used.add(node.id)
    return used


def _collect_menu_files() -> list[Path]:
    modules = ["monitor", "network", "software"]
    return [ROOT / m / "menu.py" for m in modules]


class TestMenuImportCompleteness(unittest.TestCase):
    """确保每个 menu.py 使用的 core.prompt 符号都已在顶层 import"""

    def _check_module(self, filepath: Path):
        imported = _get_imported_names(filepath)
        used = _get_called_prompt_symbols(filepath)
        missing = used - imported
        self.assertFalse(
            missing,
            f"\n{filepath.relative_to(ROOT)} 使用了 core.prompt 符号但未 import：\n"
            f"  缺失：{sorted(missing)}\n"
            f"  已导入：{sorted(imported & PROMPT_SYMBOLS)}\n"
            f"  请在文件顶部 from core.prompt import 中补上：{', '.join(sorted(missing))}"
        )

    def test_monitor_menu_imports(self):
        self._check_module(ROOT / "monitor" / "menu.py")

    def test_network_menu_imports(self):
        self._check_module(ROOT / "network" / "menu.py")

    def test_software_menu_imports(self):
        self._check_module(ROOT / "software" / "menu.py")


class TestMenuModulesImportable(unittest.TestCase):
    """确保所有 menu.py 模块能被正常 import（无 NameError / ImportError）"""

    def _try_import(self, module_name: str):
        # 用 importlib 强制重新加载，捕获所有 import 时的错误
        try:
            if module_name in sys.modules:
                del sys.modules[module_name]
            mod = importlib.import_module(module_name)
            self.assertIsNotNone(mod)
        except ImportError as e:
            self.fail(f"{module_name} 导入失败（ImportError）：{e}")
        except Exception as e:
            self.fail(f"{module_name} 导入时异常（{type(e).__name__}）：{e}")

    def test_monitor_menu_importable(self):
        self._try_import("monitor.menu")

    def test_network_menu_importable(self):
        self._try_import("network.menu")

    def test_software_menu_importable(self):
        self._try_import("software.menu")


class TestPausePresentInAllMenusUsingIt(unittest.TestCase):
    """专项：凡是调用了 pause() 的 menu.py 必须 import pause"""

    def _check_pause(self, filepath: Path):
        imported = _get_imported_names(filepath)
        used = _get_called_prompt_symbols(filepath)
        if "pause" in used:
            self.assertIn(
                "pause", imported,
                f"{filepath.relative_to(ROOT)} 调用了 pause() 但没有 import pause！"
            )

    def test_monitor(self):  self._check_pause(ROOT / "monitor"     / "menu.py")
    def test_network(self):  self._check_pause(ROOT / "network"     / "menu.py")
    def test_software(self): self._check_pause(ROOT / "software"    / "menu.py")


class TestShieldCtrlcPresentWhenUsed(unittest.TestCase):
    """专项：凡是调用了 shield_ctrlc 的 menu.py 必须 import shield_ctrlc"""

    def _check_shield(self, filepath: Path):
        imported = _get_imported_names(filepath)
        used = _get_called_prompt_symbols(filepath)
        if "shield_ctrlc" in used:
            self.assertIn(
                "shield_ctrlc", imported,
                f"{filepath.relative_to(ROOT)} 使用了 shield_ctrlc 但没有 import！"
            )

    def test_network(self):     self._check_shield(ROOT / "network"     / "menu.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
