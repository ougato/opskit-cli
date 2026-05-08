"""系统监控插件 — 注册入口"""
from __future__ import annotations

from core.module import ModuleInfo


def register() -> ModuleInfo:
    from monitor.menu import entry
    return ModuleInfo(
        key="monitor",
        description_key="module.monitor.desc",
        order=2,
        entry=entry,
        platforms=["linux", "windows", "darwin"],
    )
