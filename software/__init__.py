"""软件管理插件 — 注册入口"""
from __future__ import annotations

from core.module import ModuleInfo


def register() -> ModuleInfo:
    from software.menu import entry
    return ModuleInfo(
        key="software",
        description_key="module.software.desc",
        order=1,
        entry=entry,
        platforms=["linux", "windows", "darwin"],
    )
