"""core/paths.py 单元测试"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("VIRTUAL_ENV", str(REPO_ROOT / ".venv"))


class TestDataDir:
    def test_env_override(self, tmp_path):
        """OPSKIT_DATA_DIR 环境变量应优先"""
        import importlib
        import core.paths as _paths
        with patch.dict(os.environ, {"OPSKIT_DATA_DIR": str(tmp_path)}):
            importlib.reload(_paths)
            result = _paths.data_dir()
        assert result == tmp_path

    def test_dev_mode_returns_project_root(self):
        """开发模式（非 frozen）应返回项目根目录"""
        import core.paths as _paths
        with patch("core.paths._is_frozen", return_value=False), \
             patch.dict(os.environ, {}, clear=False):
            env_bak = os.environ.pop("OPSKIT_DATA_DIR", None)
            try:
                result = _paths.data_dir()
            finally:
                if env_bak is not None:
                    os.environ["OPSKIT_DATA_DIR"] = env_bak
        assert result == REPO_ROOT

    def test_linux_root_frozen_uses_var_lib(self):
        """Linux root 打包模式应使用 /var/lib/opskit"""
        if sys.platform != "linux":
            pytest.skip("仅 Linux 执行")
        import core.paths as _paths
        env_bak = os.environ.pop("OPSKIT_DATA_DIR", None)
        try:
            with patch("core.paths._is_frozen", return_value=True), \
                 patch("core.paths._is_root", return_value=True):
                result = _paths.data_dir()
        finally:
            if env_bak is not None:
                os.environ["OPSKIT_DATA_DIR"] = env_bak
        assert result == Path("/var/lib/opskit")

    def test_linux_nonroot_frozen_uses_platformdirs(self):
        """Linux 非 root 打包模式应使用 platformdirs 用户目录"""
        if sys.platform != "linux":
            pytest.skip("仅 Linux 执行")
        import core.paths as _paths
        env_bak = os.environ.pop("OPSKIT_DATA_DIR", None)
        try:
            with patch("core.paths._is_frozen", return_value=True), \
                 patch("core.paths._is_root", return_value=False):
                result = _paths.data_dir()
        finally:
            if env_bak is not None:
                os.environ["OPSKIT_DATA_DIR"] = env_bak
        assert "opskit" in str(result)
        assert str(result) != "/var/lib/opskit"

    def test_windows_frozen_uses_localappdata(self):
        """Windows 打包模式应使用 LOCALAPPDATA 下的用户目录"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows 执行")
        import core.paths as _paths
        fake = r"C:\Users\testuser\AppData\Local"
        env_bak = os.environ.pop("OPSKIT_DATA_DIR", None)
        try:
            with patch("core.paths._is_frozen", return_value=True), \
                 patch.dict(os.environ, {"LOCALAPPDATA": fake}):
                result = _paths.data_dir()
        finally:
            if env_bak is not None:
                os.environ["OPSKIT_DATA_DIR"] = env_bak
        assert "opskit" in str(result)
        assert "testuser" in str(result) or "Local" in str(result)
        assert "Public" not in str(result)
        assert "ProgramData" not in str(result)

    def test_windows_no_admin_required(self):
        """Windows 数据目录应在用户空间，不需要管理员权限"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows 执行")
        import core.paths as _paths
        env_bak = os.environ.pop("OPSKIT_DATA_DIR", None)
        try:
            with patch("core.paths._is_frozen", return_value=True):
                result = _paths.data_dir()
        finally:
            if env_bak is not None:
                os.environ["OPSKIT_DATA_DIR"] = env_bak
        localappdata = os.environ.get("LOCALAPPDATA", "")
        assert localappdata and str(result).startswith(localappdata)


class TestXrayPaths:
    def test_xray_paths_consistent(self):
        """xray 路径函数返回值应与 wireguard/constants.py 常量一致"""
        if sys.platform == "win32":
            pytest.skip("xray 仅 Linux/macOS")
        from core.paths import xray_binary, xray_config_file, xray_data_dir, xray_log_dir
        from wireguard.constants import XRAY_BINARY, XRAY_CONFIG_FILE

        assert str(xray_binary()) == XRAY_BINARY
        assert str(xray_config_file()) == XRAY_CONFIG_FILE
        assert "xray" in str(xray_data_dir())
        assert "xray" in str(xray_log_dir())

    def test_xray_no_hardcoded_usr_local(self):
        """不应在非 paths 模块中出现裸 /usr/local/etc/xray 字符串"""
        if sys.platform == "win32":
            pytest.skip("xray 仅 Linux/macOS")
        from core.paths import xray_config_dir
        assert str(xray_config_dir()) == "/usr/local/etc/xray"


class TestNginxWebroot:
    def test_nginx_webroot_returns_path(self):
        """nginx_webroot 应返回 Path 对象"""
        if sys.platform == "win32":
            pytest.skip("nginx 仅 Linux/macOS")
        from core.paths import nginx_webroot
        result = nginx_webroot()
        assert isinstance(result, Path)
        assert "nginx" in str(result) or "www" in str(result) or "html" in str(result)

    def test_nginx_webroot_debian_prefers_var_www(self, tmp_path):
        """Debian/Ubuntu 上 /var/www/html 存在时应优先"""
        if sys.platform == "win32":
            pytest.skip("nginx 仅 Linux/macOS")
        from core.paths import nginx_webroot
        fake_varwww = tmp_path / "var" / "www" / "html"
        fake_varwww.mkdir(parents=True)
        with patch("core.paths.Path") as MockPath:
            real_path = Path
            def side_effect(arg):
                if arg == "/var/www/html":
                    p = real_path(str(fake_varwww))
                    return p
                return real_path(arg)
            MockPath.side_effect = side_effect
            result = nginx_webroot()
        assert result is not None
