"""模块自动发现 + 加载器（双模式：源码显式登记 / 打包静态注册）"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

from core.module import ModuleInfo


# 打包后的静态注册表文件名（build.py 打包时自动生成）
_REGISTRY_MODULE = "_registry"

def _is_frozen() -> bool:
    from core.config import _is_frozen as frozen
    return frozen()


def _load_module_safe(pkg_name: str) -> ModuleInfo | None:
    """安全 import 一个包并调用 register()，失败返回 None"""
    try:
        mod: ModuleType = importlib.import_module(pkg_name)
        register_fn = getattr(mod, "register", None)
        if callable(register_fn):
            info = register_fn()
            if isinstance(info, ModuleInfo):
                return info
    except Exception:
        pass
    return None


def _discover_dev() -> list[ModuleInfo]:
    """源码模式：仅加载显式登记的官方模块"""
    from core.builtins import MODULES
    return [info for _, package in MODULES if (info := _load_module_safe(package)) is not None]



def _discover_frozen() -> list[ModuleInfo]:
    """
    打包模式：读取 _registry.py 静态注册表。

    _registry.py 格式（build.py 打包时自动生成）：
        MODULE_LIST = [
            ('software', 'software'),
            ('monitor', 'monitor'),
            ...
        ]
    """
    try:
        registry: Any = importlib.import_module(_REGISTRY_MODULE)
        module_list: list[tuple[str, str]] = getattr(registry, "MODULE_LIST", [])
    except ImportError:
        return _discover_dev()

    modules: list[ModuleInfo] = []
    for _key, pkg_name in module_list:
        info = _load_module_safe(pkg_name)
        if info is not None:
            modules.append(info)
    return modules


def discover_modules(config: dict | None = None) -> list[ModuleInfo]:
    """
    双模式加载入口：

    1. 源码模式 → 官方登记表
    2. 打包模式（单文件）→ 读取 _registry.py 静态注册表

    最终：按 order 排序，过滤当前平台不支持的模块 + 未启用的模块。
    """
    # 外部插件不进主菜单，由「插件工具」菜单进入时实时扫描加载（热插拔）
    if _is_frozen():
        modules = _discover_frozen()
    else:
        modules = _discover_dev()

    current_platform = _current_platform()

    filtered: list[ModuleInfo] = []
    for m in modules:
        if current_platform not in m.platforms:
            continue
        if not m.enabled:
            continue
        if config:
            mod_cfg = config.get("modules", {}).get(m.key, {})
            if not mod_cfg.get("enabled", True):
                continue
        filtered.append(m)

    filtered.sort(key=lambda m: m.order)
    return filtered


def builtin_module_keys() -> set[str]:
    """内置模块 key 集合（供插件 key 冲突检查）"""
    from core.builtins import MODULES
    return {key for key, _ in MODULES}


def _current_platform() -> str:
    """返回当前平台标识：'linux' / 'windows' / 'darwin'"""
    p = sys.platform
    if p.startswith("linux"):
        return "linux"
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "darwin"
    return p
