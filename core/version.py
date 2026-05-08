"""版本管理 — 单数字递增版本号 + 配置迁移"""
from __future__ import annotations

from core.constants import APP_VERSION


def current_version() -> int:
    """返回当前 OpsKit 版本（单数字递增整数）"""
    return APP_VERSION


def version_str() -> str:
    """返回版本字符串，如 'v3'"""
    return f"v{APP_VERSION}"


def is_newer(remote_version: int) -> bool:
    """判断远端版本是否比当前版本更新"""
    return remote_version > APP_VERSION


def parse_version(tag: str) -> int:
    """
    解析版本 tag 为整数。

    支持格式：'v5' / '5' / 'v5.0' → 5
    无法解析 → 返回 0
    """
    tag = tag.strip().lstrip("v")
    part = tag.split(".")[0]
    try:
        return int(part)
    except ValueError:
        return 0
