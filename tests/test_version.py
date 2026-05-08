"""core/version.py 单元测试"""
from __future__ import annotations

import pytest

from core.version import current_version, version_str, is_newer, parse_version


def test_current_version_is_int(tmp_path) -> None:
    assert isinstance(current_version(), int)
    assert current_version() >= 1


def test_version_str_format(tmp_path) -> None:
    s = version_str()
    assert s.startswith("v")
    assert s[1:].isdigit()


def test_is_newer_true(tmp_path) -> None:
    v = current_version()
    assert is_newer(v + 1) is True


def test_is_newer_false(tmp_path) -> None:
    v = current_version()
    assert is_newer(v) is False
    assert is_newer(v - 1) is False


def test_parse_version_v_prefix(tmp_path) -> None:
    assert parse_version("v5") == 5


def test_parse_version_plain(tmp_path) -> None:
    assert parse_version("3") == 3


def test_parse_version_semver(tmp_path) -> None:
    assert parse_version("v5.0") == 5


def test_parse_version_invalid(tmp_path) -> None:
    assert parse_version("abc") == 0
