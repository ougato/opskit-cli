"""安装脚本逻辑单元测试 — 验证文件命名、平台映射、repo 配置"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════════
# 1. build.py 产物文件名（不含版本号）
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildOutputName:
    def _get_output_name(self, sys_platform: str, machine: str) -> str:
        import importlib, types
        with patch("sys.platform", sys_platform), \
             patch("platform.machine", return_value=machine):
            import build as _b
            import importlib
            importlib.reload(_b)
            return _b._output_name()

    def test_linux_x64(self):
        name = self._get_output_name("linux", "x86_64")
        assert name == "opskit-linux-x64", f"期望 opskit-linux-x64，实际 {name}"

    def test_linux_arm64(self):
        name = self._get_output_name("linux", "aarch64")
        assert name == "opskit-linux-arm64", f"期望 opskit-linux-arm64，实际 {name}"

    def test_windows_x64(self):
        name = self._get_output_name("win32", "AMD64")
        assert name == "opskit-windows-x64.exe", f"期望 opskit-windows-x64.exe，实际 {name}"

    def test_darwin_arm64(self):
        name = self._get_output_name("darwin", "arm64")
        assert name == "opskit-darwin-arm64", f"期望 opskit-darwin-arm64，实际 {name}"

    def test_no_version_in_name(self):
        """产物名不应包含版本号"""
        name = self._get_output_name("linux", "x86_64")
        import re
        assert not re.search(r"-\d+", name), f"产物名不应含版本号：{name}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. updater._asset_filename 与 build.py 一致性
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssetFilename:
    def _get_asset_filename(self, os_type: str, arch: str) -> str:
        mock_info = MagicMock()
        mock_info.os_type = os_type
        mock_info.arch = arch
        with patch("core.platform.get_platform", return_value=mock_info):
            import importlib
            import core.updater as _u
            importlib.reload(_u)
            return _u._asset_filename()

    def test_linux_x64(self):
        name = self._get_asset_filename("linux", "x86_64")
        assert name == "opskit-linux-x64", f"期望 opskit-linux-x64，实际 {name}"

    def test_linux_arm64(self):
        name = self._get_asset_filename("linux", "aarch64")
        assert name == "opskit-linux-arm64", f"期望 opskit-linux-arm64，实际 {name}"

    def test_windows_x64(self):
        name = self._get_asset_filename("windows", "x86_64")
        assert name == "opskit-windows-x64.exe", f"期望 opskit-windows-x64.exe，实际 {name}"

    def test_darwin_arm64(self):
        name = self._get_asset_filename("darwin", "arm64")
        assert name == "opskit-darwin-arm64", f"期望 opskit-darwin-arm64，实际 {name}"

    def test_darwin_not_macos(self):
        """darwin 不应被映射为 macos"""
        name = self._get_asset_filename("darwin", "arm64")
        assert "macos" not in name, f"darwin 不应映射为 macos：{name}"

    def test_x86_64_maps_to_x64(self):
        """x86_64 应映射为 x64，不是 amd64"""
        name = self._get_asset_filename("linux", "x86_64")
        assert "amd64" not in name, f"x86_64 不应映射为 amd64：{name}"
        assert "x64" in name, f"x86_64 应映射为 x64：{name}"

    def test_build_and_updater_consistent_linux_x64(self):
        """build.py 与 updater.py 在 Linux x64 下产物名应相同"""
        asset = self._get_asset_filename("linux", "x86_64")
        with patch("sys.platform", "linux"), \
             patch("platform.machine", return_value="x86_64"):
            import build as _b
            import importlib
            importlib.reload(_b)
            build_name = _b._output_name()
        assert asset == build_name, f"不一致：updater={asset}，build={build_name}"

    def test_build_and_updater_consistent_darwin_arm64(self):
        """build.py 与 updater.py 在 macOS arm64 下产物名应相同"""
        asset = self._get_asset_filename("darwin", "arm64")
        with patch("sys.platform", "darwin"), \
             patch("platform.machine", return_value="arm64"):
            import build as _b
            import importlib
            importlib.reload(_b)
            build_name = _b._output_name()
        assert asset == build_name, f"不一致：updater={asset}，build={build_name}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. constants.py repo 名称
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstantsRepo:
    def test_default_repo_is_opskit_cli(self):
        from core.constants import DEFAULT_CONFIG
        repo = DEFAULT_CONFIG["update"]["repo"]
        assert repo == "ougato/opskit-cli", f"repo 应为 ougato/opskit-cli，实际：{repo}"

    def test_mirrors_have_opskit_cli(self):
        from core.constants import DEFAULT_CONFIG
        mirrors = DEFAULT_CONFIG["update"]["mirrors"]
        for m in mirrors:
            assert "opskit-cli" in m, f"mirror URL 应包含 opskit-cli：{m}"

    def test_mirrors_have_valid_https_prefix(self):
        from core.constants import DEFAULT_CONFIG
        mirrors = DEFAULT_CONFIG["update"]["mirrors"]
        for m in mirrors:
            assert m.startswith("https://"), f"mirror URL 应以 https:// 开头：{m}"

    def test_github_api_releases_template(self):
        from core.constants import GITHUB_API_RELEASES
        assert "{repo}" in GITHUB_API_RELEASES, "GITHUB_API_RELEASES 应包含 {repo} 占位符"
        url = GITHUB_API_RELEASES.format(repo="ougato/opskit-cli")
        assert "ougato/opskit-cli" in url
