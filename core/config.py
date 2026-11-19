"""配置读写与路径双模式（开发 / 打包）。"""
from __future__ import annotations

import os
import sys
from functools import wraps
from pathlib import Path
from typing import Any

import yaml
from core.resource_lock import ResourceLock
from core.bounded_yaml import MAX_BYTES, load_bounded_yaml, parse_bounded_yaml

from core.constants import (
    DEFAULT_CONFIG,
    DIR_CONFIG,
    FILE_CONFIG,
)


# ─── 运行模式检测 ─────────────────────────────────────────────────────────────

def _is_frozen() -> bool:
    """判断是否为打包后的单文件运行模式（PyInstaller / Nuitka）"""
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def get_data_dir() -> Path:
    """
    数据目录解析，委托给 core.paths.data_dir()。

    优先级（详见 core/paths.py）：
      1. 环境变量 OPSKIT_DATA_DIR
      2. 开发模式 → 项目根目录
      3. Linux root 打包 → /var/lib/opskit
      4. 其他 → platformdirs.user_data_dir("opskit")
    """
    from core.paths import data_dir
    return data_dir()


def get_config_path() -> Path:
    """返回 config/common.yaml 的完整路径"""
    return get_data_dir() / DIR_CONFIG / FILE_CONFIG


_configuration_lock = ResourceLock(lambda: get_data_dir() / ".config.lock", label="configuration")


def configuration_transaction():
    """Serialize configuration changes and authorization-dependent publication."""
    return _configuration_lock.hold()


def _serialized_configuration(function):
    @wraps(function)
    def update(*args, **kwargs):
        with configuration_transaction():
            return function(*args, **kwargs)
    return update


def get_resource_dir(relative: str) -> Path:
    """
    获取 YAML 资源目录（themes / locale / mirrors）的绝对路径。

    打包模式：通过 sys._MEIPASS（PyInstaller）或可执行文件所在目录（Nuitka）读取嵌入资源。
    开发模式：直接从项目目录读取。
    """
    if _is_frozen():
        base = getattr(sys, "_MEIPASS", Path(sys.executable).parent)
        return Path(base) / relative
    return Path(__file__).resolve().parent.parent / relative


# ─── 配置加载与保存 ───────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 覆盖 base，不删除 base 中的键"""
    from copy import deepcopy
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class StartupConfigError(ValueError):
    """Identify a malformed startup field without exposing its value."""

    def __init__(self, field: str, expected: str) -> None:
        self.field = field
        self.expected = expected
        super().__init__(f"configuration field {field} requires {expected}")


class StartupConfigPublicationError(ValueError):
    """The first configuration cannot be published safely in this directory."""


def validate_startup_config(config: dict[str, Any]) -> None:
    """Validate fields consumed during startup without rewriting user data."""
    def require(mapping: dict, key: str, expected: type, field: str, label: str) -> None:
        if key in mapping and type(mapping[key]) is not expected:
            raise StartupConfigError(field, label)

    for field in ("language", "theme"):
        require(config, field, str, field, "string")
    for field in ("log", "modules", "update", "mirror", "telemetry"):
        require(config, field, dict, field, "mapping")
    for section, fields in {
        "log": {"level": (str, "string")},
        "update": {"enabled": (bool, "boolean"), "repo": (str, "string"),
                   "github_token": (str, "string")},
        "mirror": {"region": (str, "string")},
        "telemetry": {"enabled": (bool, "boolean"), "dsn": (str, "string")},
    }.items():
        section_config = config.get(section, {})
        for key, (kind, label) in fields.items():
            require(section_config, key, kind, f"{section}.{key}", label)
    for item in config.get("modules", {}).values():
        if type(item) is not dict:
            raise StartupConfigError("modules", "module_mapping")
        require(item, "enabled", bool, "modules.*.enabled", "boolean")


def load_config(*, max_bytes=None) -> dict[str, Any]:
    """加载配置文件，不存在时返回默认配置（不写入磁盘）"""
    return _load_config_path(get_config_path(), max_bytes=max_bytes)


def _load_config_path(path: Path, *, max_bytes=None, missing_is_error=False) -> dict[str, Any]:
    """Same bounded reader for explicit supervisor-selected paths."""
    limit = MAX_BYTES if max_bytes is None else max_bytes
    if type(limit) is not int or not 0 < limit <= MAX_BYTES:
        raise ValueError("config size limit must be a positive integer at most 1MiB")
    try:
        path.lstat()
    except FileNotFoundError:
        if missing_is_error:
            raise ValueError("configuration disappeared during publication") from None
        import copy
        return copy.deepcopy(DEFAULT_CONFIG)
    except OSError:
        raise ValueError("configuration is unreadable") from None
    try:
        user_cfg = load_bounded_yaml(path, max_bytes=limit)
        if not isinstance(user_cfg, dict) or any(not isinstance(key, str) for key in user_cfg):
            raise ValueError("configuration must be a string-keyed mapping")
    except (OSError, ValueError, yaml.YAMLError):
        raise ValueError("configuration is invalid, unreadable, or exceeds size limits") from None
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


@_serialized_configuration
def save_config(config: dict[str, Any]) -> None:
    """Explicitly replace configuration; use set_config_value for single-key edits."""
    path = get_config_path()
    try:
        if not isinstance(config, dict) or any(not isinstance(key, str) for key in config):
            raise ValueError("invalid configuration root")
        text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        parse_bounded_yaml(text.encode("utf-8"))
    except (TypeError, ValueError, yaml.YAMLError):
        raise ValueError("configuration is invalid or exceeds publication limits") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    from core.secrets import atomic_write
    atomic_write(path, text)


@_serialized_configuration
def ensure_config() -> dict[str, Any]:
    """
    首次运行时自动创建默认配置：

    1. 检查 get_config_path() 是否存在
    2. 不存在 → 从 DEFAULT_CONFIG 生成，写入磁盘
    3. 已存在 → 在内存中合并默认值，保留磁盘上的原始配置
    """
    path = get_config_path()
    try:
        path.lstat()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            # The Windows private publisher commits by handle without replacing
            # an existing target; a non-cooperating writer may have created it
            # since the check above.
            from core.private_export import publish_private_file
            raw = yaml.safe_dump(DEFAULT_CONFIG, allow_unicode=True, sort_keys=False).encode("utf-8")
            parse_bounded_yaml(raw)
            try:
                publish_private_file(path, raw)
            except FileExistsError:
                # Do not misinterpret a temporary-name collision as a config
                # created by another process.
                path.lstat()
                return _load_config_path(path, missing_is_error=True)
            except ValueError:
                raise StartupConfigPublicationError from None
        else:
            from core.config_anonymous import publish_if_supported
            raw = yaml.safe_dump(DEFAULT_CONFIG, allow_unicode=True, sort_keys=False).encode("utf-8")
            parse_bounded_yaml(raw)
            try:
                published = publish_if_supported(path, raw)
            except FileExistsError:
                return _load_config_path(path, missing_is_error=True)
            except ValueError:
                raise StartupConfigPublicationError from None
            if not published:
                # The legacy fallback remains subject to an external writer
                # racing first creation on unsupported POSIX filesystems.
                save_config(DEFAULT_CONFIG)
        import copy
        return copy.deepcopy(DEFAULT_CONFIG)
    return load_config()


@_serialized_configuration
def set_config_value(config: dict[str, Any], key_path: str, value: Any) -> dict[str, Any]:
    """
    设置嵌套配置值并保存。

    key_path 使用点号分隔，如 'update.enabled'、'language'。
    """
    # The caller's snapshot may predate a completed plugin disable.
    current = load_config()
    keys = key_path.split(".")
    node = current
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    save_config(current)
    config.clear()
    config.update(current)
    return config
