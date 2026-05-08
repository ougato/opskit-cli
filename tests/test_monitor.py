"""monitor/ 模块单元测试"""
from __future__ import annotations

import pytest

from monitor.commands import (
    get_cpu,
    get_mem,
    get_disks,
    get_net,
    get_uptime,
    get_snapshot,
    fmt_bytes,
    fmt_uptime,
    fmt_percent_bar,
    CpuInfo,
    MemInfo,
    DiskPartition,
    SystemSnapshot,
)


def test_get_cpu(tmp_path) -> None:
    cpu = get_cpu()
    assert isinstance(cpu, CpuInfo)
    assert 0 <= cpu.percent <= 100
    assert cpu.count_logical >= 1


def test_get_mem(tmp_path) -> None:
    mem = get_mem()
    assert isinstance(mem, MemInfo)
    assert mem.total > 0
    assert 0 <= mem.percent <= 100


def test_get_disks(tmp_path) -> None:
    disks = get_disks()
    assert isinstance(disks, list)
    if disks:
        assert isinstance(disks[0], DiskPartition)
        assert disks[0].total > 0


def test_get_net(tmp_path) -> None:
    interfaces = get_net()
    assert isinstance(interfaces, list)


def test_get_uptime(tmp_path) -> None:
    uptime = get_uptime()
    assert uptime > 0


def test_get_snapshot(tmp_path) -> None:
    snap = get_snapshot()
    assert isinstance(snap, SystemSnapshot)
    assert snap.uptime_seconds > 0


def test_fmt_bytes(tmp_path) -> None:
    assert "KB" in fmt_bytes(2048)
    assert "MB" in fmt_bytes(2 * 1024 * 1024)
    assert "GB" in fmt_bytes(3 * 1024 ** 3)


def test_fmt_uptime(tmp_path) -> None:
    s = fmt_uptime(3661)
    assert "h" in s
    assert "m" in s


def test_fmt_percent_bar(tmp_path) -> None:
    bar = fmt_percent_bar(50.0, 10)
    assert "50.0%" in bar
    assert "█" in bar
    assert "░" in bar


def test_register_returns_module_info(tmp_path) -> None:
    from monitor import register
    from core.module import ModuleInfo
    info = register()
    assert isinstance(info, ModuleInfo)
    assert info.key == "monitor"
    assert info.order > 0
