"""Windows 安装验收测试 — 纯 Python 实现，覆盖 PowerShell + CMD 双模式"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "opskit"


@pytest.fixture(autouse=True)
def isolated_install_location(tmp_path, monkeypatch):
    """Installation checks must never create or replace a real user install."""
    local = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(sys.modules[__name__], "INSTALL_DIR", local / "opskit")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. install.ps1 文件存在且语法正确
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstallPs1Exists:
    def test_file_exists(self):
        ps1 = REPO_ROOT / "install.ps1"
        assert ps1.exists(), f"install.ps1 不存在：{ps1}"

    def test_file_not_empty(self):
        ps1 = REPO_ROOT / "install.ps1"
        assert ps1.stat().st_size > 500, "install.ps1 内容过短"

    def test_contains_download_base(self):
        ps1 = REPO_ROOT / "install.ps1"
        content = ps1.read_text(encoding="utf-8")
        assert "github.com/ougato/opskit-cli/releases" in content

    def test_contains_localappdata(self):
        ps1 = REPO_ROOT / "install.ps1"
        content = ps1.read_text(encoding="utf-8")
        assert "LOCALAPPDATA" in content

    def test_contains_path_write(self):
        ps1 = REPO_ROOT / "install.ps1"
        content = ps1.read_text(encoding="utf-8")
        assert "SetEnvironmentVariable" in content

    def test_contains_sha256(self):
        ps1 = REPO_ROOT / "install.ps1"
        content = ps1.read_text(encoding="utf-8")
        assert "SHA256" in content or "sha256" in content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. _asset_filename 在 Windows 下返回正确文件名
# ═══════════════════════════════════════════════════════════════════════════════

class TestWindowsAssetFilename:
    def test_windows_x64_filename(self):
        mock_info = MagicMock()
        mock_info.os_type = "windows"
        mock_info.arch = "x86_64"
        with patch("core.platform.get_platform", return_value=mock_info):
            import importlib
            import core.updater as _u
            importlib.reload(_u)
            name = _u._asset_filename()
        assert name == "opskit-windows-x64.exe", f"期望 opskit-windows-x64.exe，实际 {name}"

    def test_windows_filename_has_exe(self):
        mock_info = MagicMock()
        mock_info.os_type = "windows"
        mock_info.arch = "x86_64"
        with patch("core.platform.get_platform", return_value=mock_info):
            import importlib
            import core.updater as _u
            importlib.reload(_u)
            name = _u._asset_filename()
        assert name.endswith(".exe"), f"Windows 文件名应以 .exe 结尾：{name}"

    def test_windows_build_output_has_exe(self):
        with patch("sys.platform", "win32"), \
             patch("platform.machine", return_value="AMD64"):
            import importlib
            import build as _b
            importlib.reload(_b)
            name = _b._output_name()
        assert name.endswith(".exe"), f"Windows 构建产物应以 .exe 结尾：{name}"
        assert name == "opskit-windows-x64.exe", f"期望 opskit-windows-x64.exe，实际 {name}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 安装目录和 PATH 逻辑
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 平台执行")
class TestWindowsInstallDir:
    def test_install_dir_under_localappdata(self):
        localappdata = os.environ.get("LOCALAPPDATA", "")
        assert localappdata, "LOCALAPPDATA 环境变量未设置"
        expected = Path(localappdata) / "opskit"
        assert INSTALL_DIR == expected

    def test_install_dir_can_be_created(self):
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        assert INSTALL_DIR.exists()

    def test_user_path_read(self):
        user_path = os.environ.get("PATH", "")
        assert isinstance(user_path, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 热更新 pending 文件机制（Windows 路径）
# ═══════════════════════════════════════════════════════════════════════════════

class TestWindowsPendingUpdate:
    def test_pending_path_is_under_data_dir(self):
        from core.updater import _get_pending_path
        pending = _get_pending_path()
        assert "opskit.pending" in pending.name

    def test_verify_binary_rejects_small_file(self):
        from core.updater import _verify_binary
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ" + b"\x00" * 10)  # 太小
            tmp = Path(f.name)
        try:
            assert not _verify_binary(tmp), "小文件应被拒绝"
        finally:
            tmp.unlink(missing_ok=True)

    def test_verify_binary_accepts_valid_exe(self):
        from core.updater import _verify_binary
        cmd_exe = Path(r"C:\Windows\System32\cmd.exe")
        if not cmd_exe.exists():
            pytest.skip("cmd.exe 不存在")
        assert _verify_binary(cmd_exe), "cmd.exe 应通过校验"

    def test_apply_pending_skips_when_no_pending(self):
        """无 pending 文件时 apply_pending_update 应静默跳过"""
        from core.updater import _get_pending_path, apply_pending_update

        pending = _get_pending_path()
        pending.unlink(missing_ok=True)

        # 不应抛出异常
        apply_pending_update()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CMD 兼容：安装脚本中的 CMD 用法说明
# ═══════════════════════════════════════════════════════════════════════════════

class TestCmdCompatibility:
    def test_readme_has_cmd_install_command(self):
        readme = REPO_ROOT / "README.md"
        content = readme.read_text(encoding="utf-8")
        assert "curl -fsSL" in content and "opskit-windows-x64.exe" in content, "README 应包含 CMD 兼容的安装命令"

    def test_readme_has_opskit_cmd_usage(self):
        readme = REPO_ROOT / "README.md"
        content = readme.read_text(encoding="utf-8")
        assert "opskit" in content

    def test_install_ps1_no_pwsh_dependency(self):
        """install.ps1 不应依赖 pwsh 特定语法（如三元运算符）"""
        ps1 = REPO_ROOT / "install.ps1"
        content = ps1.read_text(encoding="utf-8")
        # 检查三元运算符（PowerShell 7+ 特有），install.ps1 已用 if/else 替代
        # 注意：测试文件中存在，但 install.ps1 本身不应有
        lines_with_ternary = [
            ln for ln in content.splitlines()
            if " ? " in ln and " : " in ln and not ln.strip().startswith("#")
        ]
        assert len(lines_with_ternary) == 0, \
            f"install.ps1 含 PowerShell 7+ 三元运算符（Windows PowerShell 5.1 不兼容）：{lines_with_ternary}"
