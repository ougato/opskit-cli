"""模块自动发现 + 加载器（双模式：源码显式登记 / 打包静态注册）"""
from __future__ import annotations

import importlib
import logging
import re
import sys
from types import ModuleType
from typing import Any

from core.module import ModuleInfo


# 打包后的静态注册表文件名（build.py 打包时自动生成）
_REGISTRY_MODULE = "_registry"
_log = logging.getLogger("opskit.loader")


class ModuleDiscovery(list[ModuleInfo]):
    """发现结果保留列表兼容性，同时携带可安全展示的失败标识。"""

    def __init__(self, modules: list[ModuleInfo], failures: list[str]) -> None:
        super().__init__(modules)
        self.failures = tuple(dict.fromkeys(failures))


def _safe_identifier(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", value):
        return value
    return "unknown"


def _record_failure(failures: list[str], key: object, reason: str) -> None:
    identifier = _safe_identifier(key)
    failures.append(identifier)
    # 不记录异常内容、堆栈或外部路径，避免把配置及凭据写入日志。
    try:
        _log.warning("module discovery failed: module=%s reason=%s", identifier, reason)
    except Exception:
        # 日志目录不可写时仍须保留可见提示和其他模块。
        pass


def _enabled(config: dict | None, key: str) -> bool:
    if not isinstance(config, dict):
        return True
    module_configs = config.get("modules", {})
    if not isinstance(module_configs, dict):
        return True
    module_config = module_configs.get(key, {})
    return not isinstance(module_config, dict) or module_config.get("enabled", True)

def _is_frozen() -> bool:
    from core.config import _is_frozen as frozen
    return frozen()


def _load_module_safe(pkg_name: str, key: str, failures: list[str]) -> ModuleInfo | None:
    """安全 import 并登记失败，不中断其他官方模块。"""
    try:
        mod: ModuleType = importlib.import_module(pkg_name)
        register_fn = getattr(mod, "register", None)
        if not callable(register_fn):
            _record_failure(failures, key, "missing_register")
            return None
        info = register_fn()
        if (isinstance(info, ModuleInfo) and isinstance(info.key, str) and info.key == key
                and isinstance(info.description_key, str)
                and isinstance(info.order, int) and not isinstance(info.order, bool)
                and callable(info.entry) and isinstance(info.enabled, bool)
                and isinstance(info.platforms, list)
                and all(isinstance(platform, str) for platform in info.platforms)
                and (info.label is None or isinstance(info.label, str))
                and (info.icon is None or isinstance(info.icon, str))):
            return info
        _record_failure(failures, key, "invalid_registration")
    except Exception as exc:
        _record_failure(failures, key, f"exception:{_safe_identifier(type(exc).__name__)}")
    return None


def _discover_dev(config: dict | None, failures: list[str]) -> list[ModuleInfo]:
    """源码模式：仅加载显式登记的官方模块"""
    from core.builtins import MODULES
    return [info for key, package in MODULES if _enabled(config, key)
            if (info := _load_module_safe(package, key, failures)) is not None]



def _discover_frozen(config: dict | None, failures: list[str]) -> list[ModuleInfo]:
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
        module_list = getattr(registry, "MODULE_LIST", None)
        if not isinstance(module_list, (list, tuple)):
            _record_failure(failures, _REGISTRY_MODULE, "invalid_registry")
            return _discover_dev(config, failures)
    except ModuleNotFoundError as exc:
        if exc.name != _REGISTRY_MODULE:
            _record_failure(failures, _REGISTRY_MODULE, "dependency_missing")
        return _discover_dev(config, failures)
    except Exception as exc:
        _record_failure(failures, _REGISTRY_MODULE, f"exception:{_safe_identifier(type(exc).__name__)}")
        return _discover_dev(config, failures)

    modules: list[ModuleInfo] = []
    for item in module_list:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not all(isinstance(value, str) for value in item)):
            _record_failure(failures, _REGISTRY_MODULE, "invalid_entry")
            continue
        key, pkg_name = item
        if not _enabled(config, key):
            continue
        info = _load_module_safe(pkg_name, key, failures)
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
    failures: list[str] = []
    if _is_frozen():
        modules = _discover_frozen(config, failures)
    else:
        modules = _discover_dev(config, failures)

    current_platform = _current_platform()

    filtered: list[ModuleInfo] = []
    for m in modules:
        if current_platform not in m.platforms:
            continue
        if not m.enabled:
            continue
        if not _enabled(config, m.key):
            continue
        filtered.append(m)

    filtered.sort(key=lambda m: m.order)
    return ModuleDiscovery(filtered, failures)


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
