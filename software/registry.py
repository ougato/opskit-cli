"""软件配方注册表 — 发现、注册、查询所有可用 Recipe"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Type

from software.base import Recipe

_registry: dict[str, Type[Recipe]] = {}


def register(cls: Type[Recipe]) -> Type[Recipe]:
    """装饰器：将 Recipe 子类注册到全局注册表"""
    _registry[cls.key] = cls
    return cls


def get(key: str) -> Type[Recipe] | None:
    """按 key 获取 Recipe 类，不存在返回 None"""
    _discover()
    return _registry.get(key)


def all_recipes() -> list[Type[Recipe]]:
    """返回所有已注册的 Recipe 类列表"""
    _discover()
    return list(_registry.values())


_discovered = False


def _discover() -> None:
    """自动发现 software/recipes/ 目录下的所有 Recipe"""
    global _discovered
    if _discovered:
        return
    _discovered = True

    import software.recipes  # noqa: F401  触发包初始化
    import software.recipes as recipes_pkg
    for _, modname, _ in pkgutil.iter_modules(recipes_pkg.__path__):
        importlib.import_module(f"software.recipes.{modname}")
