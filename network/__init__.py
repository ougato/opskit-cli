"""网络工具插件 — 注册入口"""
from __future__ import annotations

from core.module import ModuleInfo


def register() -> ModuleInfo:
    from network.menu import entry
    return ModuleInfo(
        key="network",
        description_key="module.network.desc",
        order=3,
        entry=entry,
        platforms=["linux", "windows", "darwin"],
    )
