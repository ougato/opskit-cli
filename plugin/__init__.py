"""插件管理插件 — 注册入口"""
from __future__ import annotations

from core.module import ModuleInfo


def register() -> ModuleInfo:
    from plugin.menu import entry
    return ModuleInfo(
        key="plugin",
        description_key="module.plugin.desc",
        order=90,
        entry=entry,
        platforms=["linux", "windows", "darwin"],
    )
