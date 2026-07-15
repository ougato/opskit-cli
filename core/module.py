"""ModuleInfo 数据类 + 插件注册协议定义"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ModuleInfo:
    key: str
    """模块唯一标识，如 'software' / 'monitor'
    菜单显示名由 get_icon(key) + t(f'menu.{key}') 动态组合"""

    description_key: str
    """i18n key，如 'module.software.desc'"""

    order: int
    """排序权重（越小越靠前）"""

    entry: Callable[[], None]
    """入口函数（点击菜单项后调用）"""

    platforms: list[str] = field(default_factory=lambda: ["linux", "windows", "darwin"])
    """支持的平台列表"""

    enabled: bool = True
    """是否启用（可通过配置文件动态关闭）"""

    label: str | None = None
    """直接指定菜单显示名（外部插件用，绕过 t(f'menu.{key}')）"""

    icon: str | None = None
    """直接指定菜单图标（外部插件用，绕过 get_icon(key)）"""
