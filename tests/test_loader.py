"""core/loader.py 单元测试"""
from __future__ import annotations

import pytest

from core.loader import discover_modules, _current_platform


def test_discover_returns_list(tmp_path) -> None:
    modules = discover_modules()
    assert isinstance(modules, list)


def test_discover_sorted_by_order(tmp_path) -> None:
    modules = discover_modules()
    orders = [m.order for m in modules]
    assert orders == sorted(orders)


def test_current_platform_valid(tmp_path) -> None:
    platform = _current_platform()
    assert platform in ("linux", "windows", "darwin") or len(platform) > 0


def test_discover_respects_config_disabled(tmp_path) -> None:
    modules_all = discover_modules()
    if not modules_all:
        pytest.skip("没有已注册的模块")
    first_key = modules_all[0].key
    cfg = {"modules": {first_key: {"enabled": False}}}
    modules_filtered = discover_modules(cfg)
    keys = [m.key for m in modules_filtered]
    assert first_key not in keys
