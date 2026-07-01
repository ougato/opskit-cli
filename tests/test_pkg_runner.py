"""core/pkg_runner.py 单元测试"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("VIRTUAL_ENV", str(REPO_ROOT / ".venv"))

from core.pkg_runner import (
    AptRunner, YumRunner, DnfRunner, ApkRunner,
    PacmanRunner, BrewRunner, ChocoRunner,
    UnsupportedPackageManager, get_runner, reset_runner,
)


def _mock_run(monkeypatch):
    mock = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("subprocess.run", mock)
    return mock


def _mock_root_run(monkeypatch):
    """needs_root 的 runner 统一走 run_as_root，这里拦截它以验证代发。"""
    mock = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("core.privilege.run_as_root", mock)
    return mock


class TestAptRunner:
    def test_install(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        AptRunner().install(["nginx", "curl"])
        mock.assert_any_call(
            ["apt-get", "install", "-y", "-qq", "nginx", "curl"],
            check=True, capture_output=True, text=True,
        )

    def test_update_index(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        AptRunner().update_index()
        mock.assert_any_call(
            ["apt-get", "update", "-qq"],
            check=False, capture_output=True, text=True,
        )

    def test_remove(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        AptRunner().remove(["nginx"])
        mock.assert_any_call(
            ["apt-get", "remove", "-y", "nginx"],
            check=False, capture_output=True, text=True,
        )


class TestYumRunner:
    def test_install(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        YumRunner().install(["wireguard-tools"])
        mock.assert_any_call(
            ["yum", "install", "-y", "wireguard-tools"],
            check=True, capture_output=True, text=True,
        )

    def test_install_extras(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        YumRunner().install_extras(["epel-release"])
        mock.assert_any_call(
            ["yum", "install", "-y", "epel-release"],
            check=False, capture_output=True, text=True,
        )


class TestDnfRunner:
    def test_install(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        DnfRunner().install(["wireguard-tools"])
        mock.assert_any_call(
            ["dnf", "install", "-y", "wireguard-tools"],
            check=True, capture_output=True, text=True,
        )

    def test_install_extras(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        DnfRunner().install_extras(["epel-release", "elrepo-release"])
        calls = [c.args[0] for c in mock.call_args_list]
        assert ["dnf", "install", "-y", "epel-release"] in calls
        assert ["dnf", "install", "-y", "elrepo-release"] in calls


class TestApkRunner:
    def test_install(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        ApkRunner().install(["python3"])
        mock.assert_any_call(
            ["apk", "add", "--no-cache", "python3"],
            check=True, capture_output=True, text=True,
        )

    def test_update_index(self, monkeypatch):
        mock = _mock_root_run(monkeypatch)
        ApkRunner().update_index()
        mock.assert_any_call(
            ["apk", "update"],
            check=False, capture_output=True, text=True,
        )

    def test_needs_root_flags(self):
        from core.pkg_runner import (
            PacmanRunner, ZypperRunner, MsiRunner, WingetRunner,
        )
        assert AptRunner.needs_root is True
        assert YumRunner.needs_root is True
        assert DnfRunner.needs_root is True
        assert ApkRunner.needs_root is True
        assert PacmanRunner.needs_root is True
        assert ZypperRunner.needs_root is True
        # 非 root / 平台自带提权的包管理器不应加 sudo
        assert BrewRunner.needs_root is False
        assert ChocoRunner.needs_root is False
        assert WingetRunner.needs_root is False
        assert MsiRunner.needs_root is False


class TestBrewRunner:
    def test_install(self, monkeypatch):
        mock = _mock_run(monkeypatch)
        BrewRunner().install(["wget"])
        mock.assert_any_call(
            ["brew", "install", "wget"],
            check=True, capture_output=True, text=True,
        )


class TestGetRunner:
    def setup_method(self):
        reset_runner()

    def teardown_method(self):
        reset_runner()

    def test_get_runner_apt(self):
        with patch("core.platform.get_platform") as mock_plat:
            mock_plat.return_value = MagicMock(pkg_manager="apt")
            runner = get_runner()
        assert isinstance(runner, AptRunner)

    def test_get_runner_yum(self):
        with patch("core.platform.get_platform") as mock_plat:
            mock_plat.return_value = MagicMock(pkg_manager="yum")
            runner = get_runner()
        assert isinstance(runner, YumRunner)

    def test_get_runner_dnf(self):
        with patch("core.platform.get_platform") as mock_plat:
            mock_plat.return_value = MagicMock(pkg_manager="dnf")
            runner = get_runner()
        assert isinstance(runner, DnfRunner)

    def test_get_runner_apk(self):
        with patch("core.platform.get_platform") as mock_plat:
            mock_plat.return_value = MagicMock(pkg_manager="apk")
            runner = get_runner()
        assert isinstance(runner, ApkRunner)

    def test_get_runner_unknown_raises(self):
        with patch("core.platform.get_platform") as mock_plat:
            mock_plat.return_value = MagicMock(pkg_manager="unknown_pm")
            with pytest.raises(UnsupportedPackageManager):
                get_runner()

    def test_get_runner_cached(self):
        """同一进程内应返回同一实例"""
        with patch("core.platform.get_platform") as mock_plat:
            mock_plat.return_value = MagicMock(pkg_manager="apt")
            r1 = get_runner()
            r2 = get_runner()
        assert r1 is r2
        assert mock_plat.call_count == 1
