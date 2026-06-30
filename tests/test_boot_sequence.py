"""启动流程单元测试 — 验证 _boot() 各修复点"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

# 防止 Linux 上 ensure_venv 触发 exec 重入导致 pytest 卡死
os.environ.setdefault("VIRTUAL_ENV", str(REPO_ROOT / ".venv"))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. check_and_apply_pending 返回 True 后，打包模式触发 exec 重启
# ═══════════════════════════════════════════════════════════════════════════════

class TestBootExecRestart:
    def test_frozen_mode_calls_execv(self):
        """打包模式（frozen=True）下 check_and_apply_pending() 返回 True 时应调用 os.execv"""
        import main as _main

        with patch("core.updater.check_and_apply_pending", return_value=True), \
             patch("core.updater.pending_version", return_value=2), \
             patch("core.theme.print_info"), \
             patch("core.i18n.t", return_value="applying v2"), \
             patch.object(sys, "frozen", True, create=True), \
             patch("os.execv") as mock_execv, \
             patch("core.config.ensure_config", return_value={"log": {}, "update": {}}), \
             patch("core.logger.init"), \
             patch("core.cleanup.init"), \
             patch("main.theme_init"), \
             patch("main.i18n_init"), \
             patch("core.platform.preflight_check", return_value=[]), \
             patch("threading.Thread"), \
             patch("main.print_info"), \
             patch("core.theme.print_warning"):
            _main._boot()

        mock_execv.assert_called_once()
        args = mock_execv.call_args[0]
        assert args[0] == sys.executable

    def test_dev_mode_no_execv(self):
        """开发模式（frozen=False）下不应调用 os.execv"""
        import main as _main

        with patch("core.updater.check_and_apply_pending", return_value=True), \
             patch("core.updater.pending_version", return_value=2), \
             patch("main.print_info"), \
             patch("core.i18n.t", return_value="applying v2"), \
             patch("os.execv") as mock_execv, \
             patch("core.config.ensure_config", return_value={"log": {}, "update": {}}), \
             patch("core.logger.init"), \
             patch("core.cleanup.init"), \
             patch("main.theme_init"), \
             patch("main.i18n_init"), \
             patch("core.platform.preflight_check", return_value=[]), \
             patch("threading.Thread"), \
             patch("core.theme.print_warning"):
            _main._boot()

        mock_execv.assert_not_called()

    def test_no_pending_no_execv(self):
        """无 pending 时不应调用 os.execv"""
        import main as _main

        with patch("core.updater.check_and_apply_pending", return_value=False), \
             patch("os.execv") as mock_execv, \
             patch("core.config.ensure_config", return_value={"log": {}, "update": {}}), \
             patch("core.logger.init"), \
             patch("core.cleanup.init"), \
             patch("main.theme_init"), \
             patch("main.i18n_init"), \
             patch("core.platform.preflight_check", return_value=[]), \
             patch("threading.Thread"), \
             patch("core.theme.print_warning"):
            _main._boot()

        mock_execv.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 更新提示版本号正确
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateVersionPrompt:
    def test_version_shown_in_prompt(self):
        """apply pending 时提示应包含正确版本号"""
        import main as _main

        printed_args = []

        def fake_print_info(msg):
            printed_args.append(msg)

        with patch("core.updater.check_and_apply_pending", return_value=True), \
             patch("core.updater.pending_version", return_value=5), \
             patch("main.print_info", side_effect=fake_print_info), \
             patch("main.t", side_effect=lambda key, **kw: f"{key}:{kw}"), \
             patch("os.execv"), \
             patch.object(sys, "frozen", True, create=True), \
             patch("core.config.ensure_config", return_value={"log": {}, "update": {}}), \
             patch("core.logger.init"), \
             patch("core.cleanup.init"), \
             patch("main.theme_init"), \
             patch("main.i18n_init"), \
             patch("core.platform.preflight_check", return_value=[]), \
             patch("threading.Thread"), \
             patch("core.theme.print_warning"):
            _main._boot()

        assert any("v5" in str(a) for a in printed_args), \
            f"提示中应含 v5，实际：{printed_args}"

    def test_version_empty_when_none(self):
        """pending_version 返回 None 时版本号为空字符串"""
        import main as _main

        printed_args = []

        def fake_print_info(msg):
            printed_args.append(msg)

        with patch("core.updater.check_and_apply_pending", return_value=True), \
             patch("core.updater.pending_version", return_value=None), \
             patch("main.print_info", side_effect=fake_print_info), \
             patch("main.t", side_effect=lambda key, **kw: f"{key}:{kw}"), \
             patch("os.execv"), \
             patch.object(sys, "frozen", True, create=True), \
             patch("core.config.ensure_config", return_value={"log": {}, "update": {}}), \
             patch("core.logger.init"), \
             patch("core.cleanup.init"), \
             patch("main.theme_init"), \
             patch("main.i18n_init"), \
             patch("core.platform.preflight_check", return_value=[]), \
             patch("threading.Thread"), \
             patch("core.theme.print_warning"):
            _main._boot()

        assert any("'version': ''" in str(a) for a in printed_args), \
            f"version 应为空字符串，实际：{printed_args}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. _on_exit 捕获 Exception（而非 ImportError）
# ═══════════════════════════════════════════════════════════════════════════════

class TestOnExit:
    def test_on_exit_catches_general_exception(self):
        """_on_exit 应捕获任意异常，不向上抛出"""
        import main as _main

        with patch("core.updater.apply_pending_update", side_effect=RuntimeError("disk full")):
            _main._on_exit({})  # 不应抛出

    def test_on_exit_calls_apply(self):
        """_on_exit 应调用 apply_pending_update"""
        import main as _main

        with patch("core.updater.apply_pending_update") as mock_apply:
            _main._on_exit({})

        mock_apply.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. _apply_unix 跨文件系统 rename 兜底
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyUnixCrossFS:
    def test_cross_fs_uses_copy2(self):
        """rename 抛出 OSError 时应 fallback 到 shutil.copy2"""
        if sys.platform == "win32":
            pytest.skip("_apply_unix 仅在 Linux/macOS 执行")

        from core.updater import _apply_unix

        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "opskit.pending"
            self_path = Path(tmp) / "opskit"

            pending.write_bytes(b"\x7fELF" + b"\x00" * 200 * 1024)
            self_path.write_bytes(b"\x7fELF" + b"\x00" * 200 * 1024)

            copy2_called = []

            original_replace = Path.replace

            def patched_replace(self_obj, target):
                if self_obj == pending:
                    raise OSError("cross-device link")
                return original_replace(self_obj, target)

            with patch.object(Path, "replace", patched_replace), \
                 patch("shutil.copy2", side_effect=lambda s, d: copy2_called.append((s, d))):
                _apply_unix(pending, self_path, 2)

            assert len(copy2_called) == 1, f"copy2 应被调用一次，实际 {copy2_called}"

    def test_same_fs_uses_replace(self):
        """同文件系统时应使用 replace（不调用 copy2）"""
        if sys.platform == "win32":
            pytest.skip("_apply_unix 仅在 Linux/macOS 执行")

        from core.updater import _apply_unix

        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "opskit.pending"
            self_path = Path(tmp) / "opskit"

            pending.write_bytes(b"\x7fELF" + b"\x00" * 200 * 1024)
            self_path.write_bytes(b"\x7fELF" + b"\x00" * 200 * 1024)

            replace_called = []
            original_replace = Path.replace

            def fake_replace(self_obj, target):
                replace_called.append(target)
                return original_replace(self_obj, target)

            with patch.object(Path, "replace", fake_replace), \
                 patch("shutil.copy2") as mock_copy2:
                _apply_unix(pending, self_path, 2)

            mock_copy2.assert_not_called()
            assert len(replace_called) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Windows 数据目录使用 LOCALAPPDATA
# ═══════════════════════════════════════════════════════════════════════════════

class TestWindowsDataDir:
    def test_windows_frozen_uses_localappdata(self):
        """Windows 打包模式应使用 LOCALAPPDATA"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows 执行")

        from core import config as _cfg

        fake_localappdata = r"C:\Users\testuser\AppData\Local"

        with patch("core.config._is_frozen", return_value=True), \
             patch.dict(os.environ, {"LOCALAPPDATA": fake_localappdata}):
            result = _cfg.get_data_dir()

        assert "testuser" in str(result) or "Local" in str(result), \
            f"应使用 LOCALAPPDATA，实际：{result}"
        assert "ProgramData" not in str(result), \
            f"不应使用 ProgramData，实际：{result}"

    def test_windows_data_dir_no_admin_needed(self):
        """Windows 数据目录应在用户目录下，不需要管理员权限"""
        from core import config as _cfg

        if sys.platform != "win32":
            pytest.skip("仅 Windows 执行")

        with patch("core.config._is_frozen", return_value=True):
            data_dir = _cfg.get_data_dir()

        localappdata = os.environ.get("LOCALAPPDATA", "")
        assert localappdata and str(data_dir).startswith(localappdata), \
            f"数据目录 {data_dir} 应在 LOCALAPPDATA={localappdata} 下"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. BOOTSTRAP_URLS 指向正确仓库
# ═══════════════════════════════════════════════════════════════════════════════

class TestBootstrapUrls:
    def test_bootstrap_urls_point_to_opskit_cli(self):
        from core.constants import BOOTSTRAP_URLS
        # GitHub 源必须指向 opskit-cli 仓库；自有 CDN 源（不含 github）不强制
        for url in BOOTSTRAP_URLS:
            if "github" in url:
                assert "opskit-cli" in url, f"BOOTSTRAP_URL 应含 opskit-cli：{url}"

    def test_bootstrap_urls_no_old_repo(self):
        from core.constants import BOOTSTRAP_URLS
        for url in BOOTSTRAP_URLS:
            assert url.count("/opskit/") == 0 or "opskit-cli" in url, \
                f"BOOTSTRAP_URL 不应指向旧仓库 /opskit/：{url}"

    def test_bootstrap_urls_valid_https(self):
        from core.constants import BOOTSTRAP_URLS
        for url in BOOTSTRAP_URLS:
            assert url.startswith("https://"), f"URL 应以 https:// 开头：{url}"


class TestVenvBootstrap:
    def test_exec_reenter_uses_venv_launcher_path(self):
        from core.venv_bootstrap import _exec_reenter

        venv_python = Path("/tmp/opskit/.venv/bin/python")
        with patch("os.execv") as mock_execv:
            _exec_reenter(venv_python)

        args = mock_execv.call_args[0]
        assert args[0] == str(venv_python)
        assert args[1][0] == str(venv_python)
