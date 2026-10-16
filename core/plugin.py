"""外部插件发现与加载 — 扫描 plugins 目录，读 plugin.yaml 清单

两种插件形态：
  - python：只读清单发现，代码在受限容器执行
  - exec：可执行程序在同样的受限容器执行

隔离原则：单个插件任何一步失败只写日志并跳过，绝不抛出影响主程序。
"""
from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.constants import FILE_PLUGIN_MANIFEST, PLUGIN_API_VERSION
from core.logger import get_logger
from core.module import ModuleInfo
from core.bounded_yaml import load_bounded_yaml
from core.paths import plugins_dir
from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
from core.plugin_trust import compute_fingerprint, is_trusted

# 插件 name 合法格式（同时用作模块 key 与 i18n/config 命名空间）
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# 插件版本合法格式（semver x.y.z，允许预发布/构建后缀）
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-+]+)?$")

# 分组 id 合法格式（与 plugin_data_dir 命名空间一致，连字符分割）
_GROUP_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# 清单必填字段
_REQUIRED_FIELDS = ("name", "version", "api_version", "kind", "entry")

# 支持的插件形态
KIND_PYTHON = "python"
KIND_EXEC = "exec"
_VALID_KINDS = (KIND_PYTHON, KIND_EXEC)
MAX_INVENTORY_ENTRIES = 4096
MAX_MANIFESTS = 256


class PluginInventoryError(RuntimeError):
    """Incomplete inventories must never masquerade as an empty catalog."""

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
    permissions: list[str] = field(default_factory=list)
    group: str | None = None                    # 分组 id（入口名称归属，开发者自定）
    group_icon: str | None = None
    group_label: dict[str, str] = field(default_factory=dict)
    provides: list[str] = field(default_factory=list)   # 对外提供的服务名（插件间服务）
    uses: list[dict[str, Any]] = field(default_factory=list)
    service_contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    root: Path = field(default_factory=Path)

    def display_label(self, lang: str) -> str | None:
        return self.label.get(lang) or self.label.get("en") or next(iter(self.label.values()), None)

    def display_group_label(self, lang: str) -> str | None:
        return (
            self.group_label.get(lang)
            or self.group_label.get("en")
            or next(iter(self.group_label.values()), None)
            or self.group
        )


def _service_contract(value):
    if not isinstance(value, dict) or set(value) != {"api_version", "methods"}:
        raise ValueError("invalid service contract fields")
    version, methods = value["api_version"], value["methods"]
    if type(version) is not int or not 1 <= version <= 65535:
        raise ValueError("invalid service business version")
    if (not isinstance(methods, list) or not 1 <= len(methods) <= 64
            or any(not isinstance(method, str) or len(method) > 128
                   or not method.isascii() or not method.isidentifier()
                   or method.startswith("_") for method in methods)
            or len(set(methods)) != len(methods)):
        raise ValueError("invalid public service methods")
    return {"api_version": version, "methods": list(methods)}


def _service_declarations(data, provides):
    contracts_raw = data.get("service_contracts", {})
    if not isinstance(contracts_raw, dict) or len(contracts_raw) > 64:
        raise ValueError("invalid service contracts")
    contracts = {}
    for service, contract in contracts_raw.items():
        if not isinstance(service, str) or not _NAME_PATTERN.fullmatch(service) or service not in provides:
            raise ValueError("contract service not provided")
        contracts[service] = _service_contract(contract)
    uses_raw = data.get("uses") or []
    if not isinstance(uses_raw, list) or len(uses_raw) > 64:
        raise ValueError("invalid service requirements")
    uses, seen = [], set()
    for requirement in uses_raw:
        if isinstance(requirement, str):
            requirement = {"service": requirement}
        if not isinstance(requirement, dict) or set(requirement) - {"service", "source", "provider", "api_version", "methods"}:
            raise ValueError("invalid service requirement fields")
        service = requirement.get("service")
        source = requirement.get("source", "")
        if (not isinstance(service, str) or not _NAME_PATTERN.fullmatch(service)
                or service in seen or not isinstance(source, str)):
            raise ValueError("invalid or duplicate service requirement")
        seen.add(service)
        parsed = {"service": service, "source": source}
        if set(requirement) & {"provider", "api_version", "methods"}:
            provider = requirement.get("provider")
            if not isinstance(provider, str) or not _NAME_PATTERN.fullmatch(provider) or provider == "platform":
                raise ValueError("invalid service provider identity")
            parsed.update(_service_contract({key: requirement[key] for key in ("api_version", "methods") if key in requirement}))
            parsed["provider"] = provider
        uses.append(parsed)
    return contracts, uses


def load_manifest(plugin_root: Path) -> PluginManifest | None:
    """读取并校验单个插件清单，失败返回 None（写日志）"""
    if plugin_root.is_symlink() or (hasattr(plugin_root, "is_junction") and plugin_root.is_junction()):
        return None
    manifest_path = plugin_root / FILE_PLUGIN_MANIFEST
    try:
        data = load_bounded_yaml(manifest_path)
    except Exception as e:
        _log.warning("plugin %s: manifest read failed (%s)", plugin_root.name, type(e).__name__)
        return None

    return _parse_manifest(data, plugin_root)


def _parse_manifest(data, plugin_root: Path) -> PluginManifest | None:
    """Normalize manifest data without filesystem access or executing plugin code."""
    if not isinstance(data, dict):
        _log.warning("plugin %s: manifest is not a mapping", plugin_root.name)
        return None

    missing = [k for k in _REQUIRED_FIELDS if not data.get(k)]
    if missing:
        _log.warning("plugin %s: manifest missing fields: %s", plugin_root.name, missing)
        return None

    name = str(data["name"])
    if not _NAME_PATTERN.fullmatch(name) or name == "platform":
        _log.warning("plugin %s: invalid name %r", plugin_root.name, name)
        return None

    version = str(data["version"])
    if not _VERSION_PATTERN.match(version):
        _log.warning("plugin %s: invalid version %r (expect semver x.y.z)", name, version)
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
    if api_version not in (1, PLUGIN_API_VERSION):
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

    permissions = data.get("permissions") or []
    if not isinstance(permissions, list):
        permissions = []

    group = data.get("group")
    if group is not None:
        group = str(group)
        if not _GROUP_PATTERN.match(group):
            _log.warning("plugin %s: invalid group %r, ignored", name, group)
            group = None
    group_label = data.get("group_label") or {}
    if not isinstance(group_label, dict):
        group_label = {}

    provides = data.get("provides") or []
    if not isinstance(provides, list):
        provides = []
    try:
        service_contracts, uses = _service_declarations(data, provides)
    except ValueError:
        _log.warning("plugin %s: invalid service declarations", name)
        return None

    if any(p not in {"network", "filesystem", "exec"} for p in permissions):
        _log.warning("plugin %s: unsupported permissions", name)
        return None
    return PluginManifest(
        name=name,
        version=version,
        api_version=api_version,
        kind=kind,
        entry=data["entry"],
        order=order,
        platforms=[str(p) for p in platforms],
        icon=data.get("icon"),
        label={str(k): str(v) for k, v in label.items()},
        description={str(k): str(v) for k, v in description.items()},
        permissions=[str(p) for p in permissions],
        group=group,
        group_icon=data.get("group_icon"),
        group_label={str(k): str(v) for k, v in group_label.items()},
        provides=[str(p) for p in provides],
        uses=uses,
        service_contracts=service_contracts,
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


def _guard_entry(name: str, fn):
    """插件菜单入口守卫：运行期任何异常（含 SystemExit）只报错回菜单，绝不杀死主程序"""

    def _entry() -> None:
        from core.prompt import UserCancel, UserExit, pause
        try:
            fn()
        except (KeyboardInterrupt, UserCancel, UserExit):
            raise
        except BaseException as e:  # 含 SystemExit：插件 sys.exit() 不得终止主程序
            from core.i18n import t
            from core.theme import print_error
            _log.error("plugin %s: entry failed: %r", name, e)
            print_error(t("plugin.entry_failed", name=name, error=str(e)))
            pause()

    return _entry


def _to_module_info(manifest: PluginManifest) -> ModuleInfo | None:
    """Construct menu metadata without importing or executing third-party code."""
    from core.i18n import current_lang
    if manifest.kind == KIND_PYTHON:
        entry = str(manifest.entry)
        if not _NAME_PATTERN.fullmatch(entry) or entry in {"core", "sys", "os", "yaml", "rich", "typer", "utils"}:
            return None
        if not (manifest.root / entry / "__init__.py").is_file():
            return None
    elif _resolve_exec_path(manifest) is None:
        return None
    def execute():
        from core.plugin_sandbox import invoke
        invoke(manifest, "entry")
    return ModuleInfo(
        key=manifest.name, description_key=f"plugin.{manifest.name}.desc",
        order=manifest.order, entry=_guard_entry(manifest.name, execute),
        platforms=manifest.platforms, label=manifest.display_label(current_lang()), icon=manifest.icon,
    )


def list_manifests() -> list[PluginManifest]:
    """列出插件目录下所有合法清单（不加载代码，供插件管理界面用）"""
    from core.plugin_sandbox import _read_inventory
    return _read_inventory(plugins_dir())


def _list_manifests(root: Path, *, select="all", value="") -> list[PluginManifest]:
    """Reader-only inventory; scan completely before returning selected identities."""
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise PluginInventoryError("linked plugin inventory root forbidden")
    if not root.exists():
        return []
    directories = []
    candidates = []
    try:
        with os.scandir(root) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_INVENTORY_ENTRIES:
                    raise PluginInventoryError("plugin inventory entry limit exceeded")
                if entry.name.startswith((".", "_")) or not entry.is_dir(follow_symlinks=False):
                    continue
                path = Path(entry.path)
                if not getattr(path, "is_junction", lambda: False)():
                    directories.append(path)
        for path in sorted(directories):
            if (path / FILE_PLUGIN_MANIFEST).exists():
                candidates.append(path)
                if len(candidates) > MAX_MANIFESTS:
                    raise PluginInventoryError("plugin inventory manifest limit exceeded")
    except OSError as error:
        raise PluginInventoryError("plugin inventory scan failed") from error
    manifests: list[PluginManifest] = []
    for path in candidates:
        manifest = load_manifest(path)
        if manifest is not None and (select == "all" or select == "name" and manifest.name == value
                                     or select == "service" and value in manifest.provides):
            manifests.append(manifest)
    return manifests


def load_plugin(manifest: PluginManifest) -> ModuleInfo | None:
    """加载单个已信任插件，未信任 / 完整性校验失败 / 加载失败返回 None（写日志）"""
    if verify_checksums(manifest.root) == CHECK_MISMATCH:
        _log.error("plugin %s: integrity check failed, refused to load", manifest.name)
        return None
    if not is_trusted(manifest.name, compute_fingerprint(manifest.root)):
        _log.warning("plugin %s: not trusted, skipped (confirm in plugin manager)", manifest.name)
        return None
    return _to_module_info(manifest)


def unload_plugin(manifest: PluginManifest) -> None:
    """No in-process plugin modules exist; revoke cached service proxies."""
    from core.plugin_services import invalidate_service_cache
    invalidate_service_cache()


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
        info = load_plugin(manifest)
        if info is None:
            continue
        seen.add(manifest.name)
        modules.append(info)
    return modules
