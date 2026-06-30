"""recipe 通用工具 — 显示名解析

menu.py 与 main.py 此前各自重复「i18n key → 本地化显示名」的组装逻辑
（共 20+ 处），且两端降级分支不完全一致。此处统一为单一函数：

    recipe_display_name(cls) -> str

解析顺序：``software.<key>`` 的 i18n 文案 → ``cls.description`` → ``cls.key``，
保证任意 recipe 都能得到非空可读名称。
"""
from __future__ import annotations

from core.i18n import t


def recipe_display_name(cls: type) -> str:
    """返回 recipe 的本地化显示名。

    Args:
        cls: recipe 类（需含 ``key``，可选 ``description``）。
    """
    key = f"software.{cls.key}"
    value = t(key)
    if value != key:
        return value
    return getattr(cls, "description", "") or cls.key
