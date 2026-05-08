"""配置读写 + 配置迁移 + 路径双模式（开发 / 打包）"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

from core.constants import (
    APP_NAME,
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
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    """加载配置文件，不存在时返回默认配置（不写入磁盘）"""
    path = get_config_path()
    if not path.exists():
        return DEFAULT_CONFIG.copy()
    with path.open("r", encoding="utf-8") as f:
        user_cfg = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(config: dict[str, Any]) -> None:
    """保存配置到磁盘"""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def ensure_config() -> dict[str, Any]:
    """
    首次运行时自动创建默认配置：

    1. 检查 get_config_path() 是否存在
    2. 不存在 → 从 DEFAULT_CONFIG 生成，写入磁盘
    3. 已存在 → 加载并与默认值合并（只增不删），如版本不一致自动迁移
    """
    path = get_config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    cfg = load_config()
    # 如果磁盘配置缺少新字段，补充默认值并写回
    merged = _deep_merge(DEFAULT_CONFIG, cfg)
    if merged != cfg:
        save_config(merged)
    return merged


def set_config_value(config: dict[str, Any], key_path: str, value: Any) -> dict[str, Any]:
    """
    设置嵌套配置值并保存。

    key_path 使用点号分隔，如 'update.enabled'、'language'。
    """
    keys = key_path.split(".")
    node = config
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    save_config(config)
    return config


# ─── 配置迁移 ─────────────────────────────────────────────────────────────────

def migrate_config(config: dict[str, Any], from_version: int, to_version: int) -> dict[str, Any]:
    """
    配置迁移策略：

    1. 每个版本定义一个 migration 函数（_migrate_v{n}）
    2. 按版本链逐步迁移：1 → 2 → 3
    3. 迁移前自动备份原配置
    4. 迁移失败 → 使用默认配置 + 保留原配置为 .bak
    5. 只增不删：新版本只添加新字段默认值，不删除旧字段
    """
    _MIGRATIONS: dict[int, Any] = {
        # 示例：1: _migrate_v1_to_v2,
    }

    current = from_version
    while current < to_version:
        fn = _MIGRATIONS.get(current)
        if fn is None:
            break
        try:
            config = fn(config)
        except Exception:
            path = get_config_path()
            bak = path.with_suffix(".bak")
            if path.exists():
                import shutil
                shutil.copy2(path, bak)
            return DEFAULT_CONFIG.copy()
        current += 1
    return config
