"""模块自动发现 + 加载器（双模式：开发扫描 / 打包静态注册）"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from core.module import ModuleInfo


# 打包后的静态注册表文件名（build.py 打包时自动生成）
_REGISTRY_MODULE = "_registry"

# 不属于插件的顶层目录/包名（扫描时跳过）
_SKIP_NAMES = frozenset({
    "core", "tests", "build", "dist", "__pycache__",
    ".git", ".windsurf", "venv", ".venv", "env",
})


def _is_frozen() -> bool:
    from core.config import _is_frozen as frozen
    return frozen()


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
    """开发模式：扫描项目根目录下所有含 register() 的包"""
    root = _get_project_root()
    modules: list[ModuleInfo] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        name = path.name
        if name in _SKIP_NAMES or name.startswith(".") or name.startswith("_"):
            continue
        init_file = path / "__init__.py"
        if not init_file.exists():
            continue
        info = _load_module_safe(name)
        if info is not None:
            modules.append(info)
    return modules


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

    1. 开发模式（源码运行）→ 扫描项目根目录
    2. 打包模式（单文件）→ 读取 _registry.py 静态注册表

    最终：按 order 排序，过滤当前平台不支持的模块 + 未启用的模块。
    """
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
