"""软件配方注册表 — 发现、注册、查询所有可用 Recipe。

配方模块导入期间只负责声明和注册，不得查询自动发现结果。
首次查询可能触发所有配方导入；导入尚未完成时查询不能得到完整结果，
跨线程查询还可能与 Python 的模块导入锁形成循环等待。
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
import threading
import time
from typing import Type

from software.base import Recipe

_registry: dict[str, Type[Recipe]] = {}
_lock = threading.RLock()
_discovery_changed = threading.Condition(_lock)
_discovering: int | None = None
_DISCOVERY_WAIT_SECONDS = 30.0


class RecipeDiscoveryTimeout(TimeoutError):
    """Another thread did not finish recipe discovery within the wait limit."""


class RecipeDiscoveryReentryError(RuntimeError):
    """A registry query occurred during a recipe import or recursive discovery."""


def register(cls: Type[Recipe]) -> Type[Recipe]:
    """装饰器：将 Recipe 子类注册到全局注册表"""
    with _lock:
        _registry[cls.key] = cls
    return cls


def get(key: str) -> Type[Recipe] | None:
    """按 key 获取 Recipe 类，不存在返回 None"""
    _discover()
    with _lock:
        return _registry.get(key)


def all_recipes() -> list[Type[Recipe]]:
    """返回所有已注册的 Recipe 类列表"""
    _discover()
    with _lock:
        return list(_registry.values())


_discovered = False


def _discover() -> None:
    """自动发现 software/recipes/ 目录下的所有 Recipe"""
    global _discovered, _discovering
    # A direct import can enter discovery before its own module has registered
    # its class. Python permits the importing thread to re-import that module,
    # which otherwise makes a partial registry look like completed discovery.
    # Check before waiting too: a concurrent discovery may hold the module lock
    # that the importing thread needs to release.
    frame = sys._getframe(1)
    try:
        while frame is not None:
            module_name = frame.f_globals.get("__name__", "")
            if (frame.f_code.co_name == "<module>" and isinstance(module_name, str)
                    and module_name.startswith("software.recipes.")):
                raise RecipeDiscoveryReentryError(
                    "Recipe registry cannot be queried during recipe module import"
                )
            frame = frame.f_back
    finally:
        del frame
    with _discovery_changed:
        deadline = None
        while _discovering is not None:
            if _discovering == threading.get_ident():
                raise RecipeDiscoveryReentryError(
                    "Recipe registry cannot be queried during recipe discovery"
                )
            if deadline is None:
                deadline = time.monotonic() + _DISCOVERY_WAIT_SECONDS
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecipeDiscoveryTimeout(
                    f"Recipe discovery did not finish within {_DISCOVERY_WAIT_SECONDS:g} seconds"
                )
            _discovery_changed.wait(remaining)
        if _discovered:
            return
        _discovering = threading.get_ident()
    try:
        import software.recipes as recipes_pkg
        for _, modname, _ in pkgutil.iter_modules(recipes_pkg.__path__):
            importlib.import_module(f"software.recipes.{modname}")
    except BaseException:
        with _discovery_changed:
            _discovering = None
            _discovery_changed.notify_all()
        raise
    else:
        with _discovery_changed:
            _discovered = True
            _discovering = None
            _discovery_changed.notify_all()
