"""
菜单 UI 自动化测试
验证：
1. 所有 menu.py entry() 在 UserCancel 时正确返回（不崩溃、不传播异常）
2. select() 调用都有 back_label 参数
3. 没有硬编码 emoji（所有图标通过 get_icon() 获取）
4. confirm/text_input 使用新签名（breadcrumb + prompt）
5. Ctrl+C 在子菜单返回上层，主菜单退出程序
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_IN_CI = os.environ.get("CI") == "true" or os.environ.get("GITLAB_CI") == "true"

# 确保项目根目录在 sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ─── 初始化 core 层（避免 YAML 未加载报错）─────────────────────────────────────

os.chdir(ROOT)

import core.config as _cfg_mod
_cfg_mod.ensure_config()

from core.theme import init as _theme_init
_theme_init()

from core.i18n import init as _i18n_init
_i18n_init()


# ─── 辅助：枚举所有模块 menu.py ────────────────────────────────────────────────

_MODULE_DIRS = [
    "software", "monitor", "network",
]

def _get_menu_path(module_name: str) -> Path:
    return ROOT / module_name / "menu.py"


# ─── 静态分析测试 ──────────────────────────────────────────────────────────────

class TestMenuStaticAnalysis(unittest.TestCase):
    """静态分析所有 menu.py，不实际运行业务逻辑"""

    def _read_source(self, module_name: str) -> str:
        p = _get_menu_path(module_name)
        self.assertTrue(p.exists(), f"{module_name}/menu.py 不存在")
        return p.read_text(encoding="utf-8")

    def test_no_hardcoded_emoji_in_labels(self):
        """choices label 中不得出现硬编码 emoji 字符串（应通过 get_icon() 获取）"""
        # 常见 emoji Unicode 范围检测（仅检查 f-string label 中的直接 emoji）
        import re
        # 匹配 f"... {emoji_char} ..." 形式的硬编码 emoji
        emoji_pattern = re.compile(
            r'["\'](.*?[\U00010000-\U0010ffff\u2600-\u27BF\uFE00-\uFE0F]+.*?)["\']'
        )
        for mod in _MODULE_DIRS:
            src = self._read_source(mod)
            # 排除注释行
            lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
            clean = "\n".join(lines)
            # 只检查 "label" 键赋值中的 emoji
            label_lines = [l for l in clean.splitlines() if '"label"' in l or "'label'" in l]
            for line in label_lines:
                # get_icon() 调用的行是合法的，跳过
                if "get_icon(" in line:
                    continue
                m = emoji_pattern.search(line)
                if m:
                    self.fail(
                        f"{mod}/menu.py 存在硬编码 emoji in label: {line.strip()}"
                    )

    def test_no_key_zero_in_choices(self):
        """choices 列表中不得出现 key='0' 或 key=\"0\"（应改用 back_label 参数）"""
        import re
        pattern = re.compile(r'"key"\s*:\s*"0"|\'key\'\s*:\s*\'0\'')
        for mod in _MODULE_DIRS:
            src = self._read_source(mod)
            lines = src.splitlines()
            for i, line in enumerate(lines, 1):
                if line.strip().startswith("#"):
                    continue
                if pattern.search(line):
                    self.fail(
                        f"{mod}/menu.py line {i}: 发现 key=0，应改用 back_label 参数\n  {line.strip()}"
                    )

    def test_back_label_present_in_select_calls(self):
        """所有 select() 调用必须有 back_label 参数"""
        import re
        # 找到 select( ... ) 调用块，检查是否包含 back_label
        for mod in _MODULE_DIRS:
            src = self._read_source(mod)
            # 简单检查：找所有 select( 开始的块
            # 用 AST 更准确
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name != "select":
                    continue
                kwargs = {kw.arg for kw in node.keywords}
                self.assertIn(
                    "back_label", kwargs,
                    f"{mod}/menu.py line {node.lineno}: select() 缺少 back_label 参数"
                )

    def test_usercancal_imported(self):
        """所有 menu.py 必须 import UserCancel"""
        for mod in _MODULE_DIRS:
            src = self._read_source(mod)
            self.assertIn(
                "UserCancel", src,
                f"{mod}/menu.py 未 import UserCancel"
            )

    def test_confirm_new_signature(self):
        """confirm() 调用必须使用新签名（有 breadcrumb= 或 prompt= 关键字参数）"""
        for mod in _MODULE_DIRS:
            src = self._read_source(mod)
            if "confirm(" not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name != "confirm":
                    continue
                kwargs = {kw.arg for kw in node.keywords}
                # 新签名必须有 breadcrumb 和 prompt
                self.assertIn(
                    "breadcrumb", kwargs,
                    f"{mod}/menu.py line {node.lineno}: confirm() 缺少 breadcrumb= 参数"
                )
                self.assertIn(
                    "prompt", kwargs,
                    f"{mod}/menu.py line {node.lineno}: confirm() 缺少 prompt= 参数"
                )

    def test_text_input_new_signature(self):
        """text_input() 调用必须使用新签名（有 breadcrumb= 和 prompt=）"""
        for mod in _MODULE_DIRS:
            src = self._read_source(mod)
            if "text_input(" not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name != "text_input":
                    continue
                kwargs = {kw.arg for kw in node.keywords}
                self.assertIn(
                    "breadcrumb", kwargs,
                    f"{mod}/menu.py line {node.lineno}: text_input() 缺少 breadcrumb= 参数"
                )
                self.assertIn(
                    "prompt", kwargs,
                    f"{mod}/menu.py line {node.lineno}: text_input() 缺少 prompt= 参数"
                )


# ─── 运行时行为测试 ────────────────────────────────────────────────────────────

def _make_select_raise(exc_class):
    """返回一个 select mock，第一次调用就抛 exc_class"""
    def _select(*args, **kwargs):
        raise exc_class()
    return _select


class TestMenuUserCancel(unittest.TestCase):
    """验证每个模块 entry() 在 UserCancel 时能正常返回（不向上传播）"""

    def _run_entry_with_cancel(self, module_name: str):
        from core.prompt import UserCancel
        mod = importlib.import_module(f"{module_name}.menu")
        with patch(f"{module_name}.menu.select", side_effect=UserCancel):
            # entry() 应该捕获 UserCancel 并返回，不再传播
            try:
                mod.entry()
            except UserCancel:
                self.fail(
                    f"{module_name}.menu.entry() 将 UserCancel 传播到了调用方，应在内部捕获"
                )
            except Exception as e:
                # 允许业务异常（如命令未实现），但不允许 UserCancel 传播
                pass

    def test_software_entry_usercancal(self):
        self._run_entry_with_cancel("software")

    def test_monitor_entry_usercancal(self):
        self._run_entry_with_cancel("monitor")

    def test_network_entry_usercancal(self):
        self._run_entry_with_cancel("network")



class TestMenuSelectReturnsNone(unittest.TestCase):
    """验证每个模块 entry() 在 select() 返回 None（按 0）时能正常返回"""

    def _run_entry_with_none(self, module_name: str):
        mod = importlib.import_module(f"{module_name}.menu")
        with patch(f"{module_name}.menu.select", return_value=None):
            try:
                mod.entry()
            except SystemExit:
                raise
            except Exception:
                pass  # 允许业务异常

    def test_software_entry_none(self):
        self._run_entry_with_none("software")

    def test_monitor_entry_none(self):
        self._run_entry_with_none("monitor")

    def test_network_entry_none(self):
        self._run_entry_with_none("network")



class TestSelectBackLabelPassed(unittest.TestCase):
    """验证每个模块 entry() 实际调用 select() 时传递了 back_label 参数"""

    def _capture_select_calls(self, module_name: str):
        from core.prompt import UserCancel
        mod = importlib.import_module(f"{module_name}.menu")
        captured = []

        def mock_select(*args, **kwargs):
            captured.append(kwargs)
            raise UserCancel()  # 第一次调用后立即退出

        with patch(f"{module_name}.menu.select", side_effect=mock_select):
            try:
                mod.entry()
            except Exception:
                pass

        return captured

    def _assert_back_label(self, module_name: str):
        calls = self._capture_select_calls(module_name)
        self.assertTrue(len(calls) > 0, f"{module_name}.menu.entry() 没有调用 select()")
        for c in calls:
            self.assertIn(
                "back_label", c,
                f"{module_name}.menu entry() 的 select() 调用缺少 back_label"
            )
            self.assertTrue(
                c["back_label"],
                f"{module_name}.menu entry() 的 back_label 为空字符串"
            )

    def test_software_back_label(self):
        self._assert_back_label("software")

    def test_monitor_back_label(self):
        self._assert_back_label("monitor")

    def test_network_back_label(self):
        self._assert_back_label("network")



class TestIconTokensExist(unittest.TestCase):
    """验证所有 menu.py 中调用的 get_icon(token) 在主题 YAML 中都有定义"""

    def setUp(self):
        from core.theme import get_icon
        self.get_icon = get_icon

    def test_all_icon_tokens_resolve(self):
        """get_icon() 对未知 token 返回 '•'，检测到 '•' 说明 token 缺失"""
        import re
        pattern = re.compile(r"get_icon\(['\"](\w+)['\"]\)")
        missing = {}
        for mod in _MODULE_DIRS:
            p = _get_menu_path(mod)
            src = p.read_text(encoding="utf-8")
            tokens = pattern.findall(src)
            for token in tokens:
                result = self.get_icon(token)
                if result == "•":
                    missing.setdefault(mod, []).append(token)

        # 也检查 main.py
        main_src = (ROOT / "main.py").read_text(encoding="utf-8")
        for token in pattern.findall(main_src):
            result = self.get_icon(token)
            if result == "•":
                missing.setdefault("main", []).append(token)

        if missing:
            msg = "\n".join(
                f"  {mod}: {', '.join(tokens)}"
                for mod, tokens in missing.items()
            )
            self.fail(f"以下 get_icon() token 在主题 YAML 中未定义（返回 '•'）:\n{msg}")


@pytest.mark.skipif(_IN_CI, reason="CI 无 TTY，主菜单测试跳过")
class TestMainMenuUserCancel(unittest.TestCase):
    """验证主菜单 Ctrl+C 调用 _on_exit 后退出，不传播到更上层"""

    def test_main_menu_usercancal_calls_on_exit(self):
        import main as main_mod
        from core.prompt import UserCancel

        with patch("main.select", side_effect=UserCancel), \
             patch("main._on_exit") as mock_exit, \
             patch("main.discover_modules", return_value=[]):
            main_mod._main_menu({})
            mock_exit.assert_called_once()

    def test_main_menu_key_none_calls_on_exit(self):
        import main as main_mod

        with patch("main.select", return_value=None), \
             patch("main._on_exit") as mock_exit, \
             patch("main.discover_modules", return_value=[]):
            main_mod._main_menu({})
            mock_exit.assert_called_once()


@pytest.mark.skipif(_IN_CI, reason="CI 无 TTY，主菜单测试跳过")
class TestSubMenuUserCancelDoesNotPropagate(unittest.TestCase):
    """验证子模块 entry() 内的 UserCancel 不传播到 main._main_menu"""

    def test_entry_usercancal_caught_by_main(self):
        """main._main_menu 内部 entry() 抛出 UserCancel，主菜单应继续循环而非崩溃"""
        import main as main_mod
        from core.prompt import UserCancel

        call_count = {"n": 0}

        def mock_select(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "1"   # 第一次选择模块 1
            raise UserCancel  # 第二次 Ctrl+C 退出主菜单

        fake_module = MagicMock()
        fake_module.key = "software"
        fake_module.entry.side_effect = UserCancel()

        with patch("main.select", side_effect=mock_select), \
             patch("main._on_exit"), \
             patch("main.discover_modules", return_value=[fake_module]):
            try:
                main_mod._main_menu({})
            except UserCancel:
                self.fail("UserCancel 从 entry() 传播到了 _main_menu 外部")


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_pad_label_does_not_double_space_after_variation_selector() -> None:
    from core.prompt import _pad_label

    assert _pad_label("🛠️ x-ui") == "🛠️ x-ui"
    assert _pad_label("🛠️x-ui") == "🛠️ x-ui"


def test_select_returns_none_on_eof_without_busy_loop(monkeypatch) -> None:
    """非 TTY / EOF 时 _read_key 返回空串，select 必须立即返回 None 而非空转占满 CPU。"""
    from core import prompt

    monkeypatch.setattr(prompt, "_read_key", lambda: "")
    result = prompt.select(
        breadcrumb=["x"],
        subtitle="s",
        choices=[{"key": "1", "label": "a"}],
        theme_key="root",
    )
    assert result is None


def test_confirm_returns_false_on_eof_without_busy_loop(monkeypatch) -> None:
    from core import prompt

    monkeypatch.setattr(prompt, "_read_key", lambda: "")
    assert prompt.confirm(breadcrumb=["x"], prompt="ok?") is False


class TestTextInputInfoLines:
    """text_input info_lines：标签与输入行之间的信息行渲染"""

    def test_info_lines_rendered(self, monkeypatch, capsys):
        from core import prompt as prompt_mod

        monkeypatch.setattr(prompt_mod.os, "system", lambda cmd: 0)
        monkeypatch.setattr(prompt_mod, "_read_line", lambda: "")
        result = prompt_mod.text_input(
            breadcrumb=["OpsKit", "Test"],
            prompt="构建版本",
            default="1.2.4",
            info_lines=["当前 1.2.3"],
        )
        out = capsys.readouterr().out
        assert result == "1.2.4"
        assert "当前 1.2.3" in out
        assert "(1.2.4)" in out
