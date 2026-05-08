"""network/ + core/utils 单元测试"""
from __future__ import annotations

import sys

import pytest


# ─── network ─────────────────────────────────────────────────────────────────

def test_network_register(tmp_path) -> None:
    from network import register
    from core.module import ModuleInfo
    info = register()
    assert isinstance(info, ModuleInfo)
    assert info.key == "network"


def test_dns_lookup(tmp_path) -> None:
    from network.commands import dns_lookup
    result = dns_lookup("localhost")
    assert isinstance(result.addresses, list)


def test_get_local_ip(tmp_path) -> None:
    from network.commands import get_local_ip
    ip = get_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0


def test_scan_port_localhost(tmp_path) -> None:
    from network.commands import scan_port
    result = scan_port("127.0.0.1", 9)  # discard port — 通常 closed
    assert isinstance(result.open, bool)


def test_scan_ports_returns_list(tmp_path) -> None:
    from network.commands import scan_ports
    results = scan_ports("127.0.0.1", [80, 443], timeout=0.3)
    assert isinstance(results, list)
    assert len(results) == 2


# ─── core/utils ──────────────────────────────────────────────────────────────

def test_fmt_bytes(tmp_path) -> None:
    from core.utils import fmt_bytes
    assert "KB" in fmt_bytes(2048)
    assert "MB" in fmt_bytes(2 * 1024 * 1024)


def test_fmt_uptime(tmp_path) -> None:
    from core.utils import fmt_uptime
    assert "m" in fmt_uptime(3661)


def test_fmt_percent_bar(tmp_path) -> None:
    from core.utils import fmt_percent_bar
    bar = fmt_percent_bar(50.0, 10)
    assert "50.0%" in bar
