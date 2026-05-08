"""core/platform.py 单元测试"""
from __future__ import annotations

import pytest

from core.platform import (
    PlatformInfo,
    get_platform,
    preflight_check,
    check_disk_space,
    _arch,
    _os_type,
)


def test_get_platform_returns_info(tmp_path) -> None:
    info = get_platform()
    assert isinstance(info, PlatformInfo)


def test_os_type_valid(tmp_path) -> None:
    ot = _os_type()
    assert ot in ("linux", "windows", "darwin") or len(ot) > 0


def test_arch_valid(tmp_path) -> None:
    arch = _arch()
    assert len(arch) > 0


def test_platform_fields(tmp_path) -> None:
    info = get_platform()
    assert info.os_type in ("linux", "windows", "darwin") or len(info.os_type) > 0
    assert len(info.arch) > 0
    assert len(info.python_version) > 0
    assert info.disk_free_bytes >= 0


def test_preflight_check_returns_list(tmp_path) -> None:
    issues = preflight_check()
    assert isinstance(issues, list)


def test_check_disk_space_large(tmp_path) -> None:
    assert check_disk_space(0) is True


def test_check_disk_space_impossible(tmp_path) -> None:
    # 1 PB 必然不足
    assert check_disk_space(1024 ** 5) is False
