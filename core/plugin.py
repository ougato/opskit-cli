"""外部插件发现与加载 — 扫描 plugins 目录，读 plugin.yaml 清单

两种插件形态：
  - python：进程内加载，import 清单 entry 指向的包并调用 register() -> ModuleInfo
  - exec：  子进程执行，菜单项被选中后以继承 stdio 的子进程运行 entry 可执行文件

隔离原则：单个插件任何一步失败只写日志并跳过，绝不抛出影响主程序。
"""
from __future__ import annotations

import importlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.constants import FILE_PLUGIN_MANIFEST, PLUGIN_API_VERSION
from core.logger import get_logger
from core.module import ModuleInfo
from core.paths import plugins_dir

# 插件 name 合法格式（同时用作模块 key 与 i18n/config 命名空间）
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# 清单必填字段
_REQUIRED_FIELDS = ("name", "version", "api_version", "kind", "entry")

# 支持的插件形态
KIND_PYTHON = "python"
KIND_EXEC = "exec"
_VALID_KINDS = (KIND_PYTHON, KIND_EXEC)

# 外部插件默认排序权重（内置模块之后）
_DEFAULT_ORDER = 50

_log = get_logger("opskit.plugin")


@dataclass
class PluginManifest:
    """plugin.yaml 解析结果"""

    name: str
    version: str
    api_version: int
    kind: str
    entry: Any                       # python: 包名(str); exec: 相对路径(str) 或 {platform: path}
    order: int = _DEFAULT_ORDER
    platforms: list[str] = field(default_factory=lambda: ["linux", "windows", "darwin"])
    icon: str | None = None
    label: dict[str, str] = field(default_factory=dict)
    description: dict[str, str] = field(default_factory=dict)
    root: Path = field(default_factory=Path)

    def display_label(self, lang: str) -> str | None:
        return self.label.get(lang) or self.label.get("en") or next(iter(self.label.values()), None)


def load_manifest(plugin_root: Path) -> PluginManifest | None:
    """读取并校验单个插件清单，失败返回 None（写日志）"""
    manifest_path = plugin_root / FILE_PLUGIN_MANIFEST
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        _log.warning("plugin %s: manifest read failed: %s", plugin_root.name, e)
        return None

    if not isinstance(data, dict):
        _log.warning("plugin %s: manifest is not a mapping", plugin_root.name)
        return None

    missing = [k for k in _REQUIRED_FIELDS if not data.get(k)]
    if missing:
        _log.warning("plugin %s: manifest missing fields: %s", plugin_root.name, missing)
        return None

    name = str(data["name"])
    if not _NAME_PATTERN.match(name):
        _log.warning("plugin %s: invalid name %r", plugin_root.name, name)
        return None

    kind = str(data["kind"])
    if kind not in _VALID_KINDS:
        _log.warning("plugin %s: invalid kind %r", name, kind)
        return None

    try:
        api_version = int(data["api_version"])
    except (TypeError, ValueError):
        _log.warning("plugin %s: api_version is not an integer", name)
        return None
    if api_version != PLUGIN_API_VERSION:
        _log.warning(
            "plugin %s: api_version %s incompatible with SDK %s, skipped",
            name, api_version, PLUGIN_API_VERSION,
        )
        return None

    platforms = data.get("platforms") or ["linux", "windows", "darwin"]
    if not isinstance(platforms, list):
        _log.warning("plugin %s: platforms is not a list", name)
        return None

    label = data.get("label") or {}
    description = data.get("description") or {}
    if not isinstance(label, dict):
        label = {}
    if not isinstance(description, dict):
        description = {}

    try:
        order = int(data.get("order", _DEFAULT_ORDER))
    except (TypeError, ValueError):
        order = _DEFAULT_ORDER

    return PluginManifest(
        name=name,
        version=str(data["version"]),
        api_version=api_version,
        kind=kind,
        entry=data["entry"],
        order=order,
        platforms=[str(p) for p in platforms],
        icon=data.get("icon"),
        label={str(k): str(v) for k, v in label.items()},
        description={str(k): str(v) for k, v in description.items()},
        root=plugin_root,
    )


def _resolve_exec_path(manifest: PluginManifest) -> Path | None:
    """解析 exec 插件的可执行文件路径（支持按平台映射）"""
    from core.loader import _current_platform

    entry = manifest.entry
    if isinstance(entry, dict):
        platform = _current_platform()
        entry = entry.get(platform)
        if not entry:
            _log.warning("plugin %s: no exec entry for platform %s", manifest.name, platform)
            return None
    path = (manifest.root / str(entry)).resolve()
    try:
        path.relative_to(manifest.root.resolve())
    except ValueError:
        _log.warning("plugin %s: exec entry escapes plugin dir: %s", manifest.name, path)
        return None
    if not path.exists():
        _log.warning("plugin %s: exec entry not found: %s", manifest.name, path)
        return None
    return path


def _make_exec_entry(manifest: PluginManifest, exec_path: Path):
    """构造 exec 插件的菜单入口：子进程继承 stdio 运行，结束后等待按键"""

    def _entry() -> None:
        from core.prompt import clear_screen, pause
        clear_screen()
        try:
            subprocess.call([str(exec_path)], cwd=str(manifest.root))
        except OSError as e:
            from core.i18n import t
            from core.theme import print_error
            print_error(t("plugin.exec_failed", name=manifest.name, error=str(e)))
        pause()

    return _entry


def _load_python_plugin(manifest: PluginManifest) -> ModuleInfo | None:
    """进程内加载 python 插件：sys.path 注入插件目录 → import → register()"""
    root_str = str(manifest.root.resolve())
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        mod = importlib.import_module(str(manifest.entry))
        register_fn = getattr(mod, "register", None)
        if not callable(register_fn):
            _log.warning("plugin %s: entry package has no register()", manifest.name)
            return None
        info = register_fn()
        if not isinstance(info, ModuleInfo):
            _log.warning("plugin %s: register() did not return ModuleInfo", manifest.name)
            return None
        return info
    except Exception as e:
        _log.warning("plugin %s: import/register failed: %s", manifest.name, e)
        return None


def _to_module_info(manifest: PluginManifest) -> ModuleInfo | None:
    """清单 → ModuleInfo（python: register() 结果 + 清单覆盖；exec: 直接构造）"""
    from core.i18n import current_lang

    label = manifest.display_label(current_lang())

    if manifest.kind == KIND_PYTHON:
        info = _load_python_plugin(manifest)
        if info is None:
            return None
        info.key = manifest.name
        info.order = manifest.order
        info.platforms = manifest.platforms
        if label and not info.label:
            info.label = label
        if manifest.icon and not info.icon:
            info.icon = manifest.icon
        return info

    exec_path = _resolve_exec_path(manifest)
    if exec_path is None:
        return None
    return ModuleInfo(
        key=manifest.name,
        description_key=f"plugin.{manifest.name}.desc",
        order=manifest.order,
        entry=_make_exec_entry(manifest, exec_path),
        platforms=manifest.platforms,
        label=label,
        icon=manifest.icon,
    )


def list_manifests() -> list[PluginManifest]:
    """列出插件目录下所有合法清单（不加载代码，供插件管理界面用）"""
    root = plugins_dir()
    if not root.is_dir():
        return []
    manifests: list[PluginManifest] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith(".") or path.name.startswith("_"):
            continue
        if not (path / FILE_PLUGIN_MANIFEST).exists():
            continue
        manifest = load_manifest(path)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def discover_plugins(builtin_keys: set[str] | None = None) -> list[ModuleInfo]:
    """
    发现并加载全部外部插件，返回 ModuleInfo 列表。

    builtin_keys：已注册的内置模块 key 集合，冲突的插件跳过。
    任何插件失败只写日志跳过，绝不抛出。
    """
    builtin_keys = builtin_keys or set()
    modules: list[ModuleInfo] = []
    seen: set[str] = set()
    for manifest in list_manifests():
        if manifest.name in builtin_keys or manifest.name in seen:
            _log.warning("plugin %s: key conflict, skipped", manifest.name)
            continue
        info = _to_module_info(manifest)
        if info is None:
            continue
        seen.add(manifest.name)
        modules.append(info)
    return modules
