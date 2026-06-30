"""版本解析骨架 — 统一 8 个 recipe 的 version_list 四级降级

各版本化 recipe 此前各自重复同一套四级降级骨架：

    1. 本地缓存未过期 → 直接返回
    2. 在线 API 获取 → 成功则写缓存并返回
    3. 在线失败 → 过期缓存兜底
    4. 彻底无缓存 → 内置 fallback

仅「在线获取」步骤各 recipe 解析逻辑不同（deb Packages.gz / go.dev /
Adoptium 并发 / nodejs dist 等），故以组合方式收敛：调用方传入 ``fetch``
闭包（返回已清洗、已排序的最终列表），骨架负责缓存读写与兜底，
``version_list`` 对外签名与返回值保持不变。
"""
from __future__ import annotations

from typing import Callable


def _has_numeric(versions: list[str]) -> bool:
    """列表中是否存在以数字开头的有效版本（过滤 ['latest'] 占位）。"""
    return any(v[:1].isdigit() for v in versions if v)


def resolve_versions(
    key: str,
    fetch: Callable[[], list[str]],
    fallback: list[str],
) -> list[str]:
    """四级降级解析可安装版本列表。

    Args:
        key: 缓存键（如 ``"redis"`` / ``"golang"``）。
        fetch: 在线获取闭包，返回已清洗排序的最终版本列表；异常或空表示失败。
        fallback: 内置兜底版本列表。
    """
    from core.version_cache import (
        get_cached_versions,
        get_cached_versions_stale,
        update_cache,
    )

    cached = get_cached_versions(key)
    if cached and _has_numeric(cached):
        return cached

    try:
        fetched = list(fetch() or [])
    except Exception:
        fetched = []
    if fetched:
        update_cache(key, fetched)
        return fetched

    stale = get_cached_versions_stale(key)
    if stale and _has_numeric(stale):
        return stale

    return list(fallback)
