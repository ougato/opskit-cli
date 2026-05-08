"""版本缓存层 — 本地缓存 + 后台静默刷新 + 多级兜底"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_loaded = False


# ─── 缓存文件读写 ─────────────────────────────────────────────────────────────

def _get_cache_path() -> Path:
    from core.config import get_data_dir
    from core.constants import FILE_VERSION_CACHE
    return get_data_dir() / "cache" / FILE_VERSION_CACHE


def _load_cache() -> dict[str, Any]:
    """读取版本缓存文件，解析失败则删除损坏文件"""
    path = _get_cache_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("版本缓存文件损坏，已删除: %s", e)
        path.unlink(missing_ok=True)
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    """原子写入版本缓存文件"""
    path = _get_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        tmp.replace(path)
    except Exception as e:
        logger.warning("写入版本缓存失败: %s", e)
        tmp.unlink(missing_ok=True)


def _ensure_loaded() -> None:
    """确保缓存已从磁盘加载"""
    global _cache, _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        _cache = _load_cache()
        _loaded = True


# ─── 对外接口 ─────────────────────────────────────────────────────────────────

def get_cached_versions(recipe_key: str) -> list[str] | None:
    """获取缓存的版本列表，未缓存或已过期返回 None

    三级过期策略：
    - < 1h：直接返回缓存
    - 1h ~ 24h：返回缓存（旧数据），后台刷新
    - > 24h：返回 None（触发前台获取）
    """
    from core.constants import VERSION_CACHE_TTL, VERSION_CACHE_STALE_TTL
    _ensure_loaded()

    entry = _cache.get(recipe_key)
    if entry is None:
        return None

    age = time.time() - entry.get("timestamp", 0)
    versions = entry.get("versions", [])

    if not versions:
        return None

    if age < VERSION_CACHE_TTL:
        return versions

    if age < VERSION_CACHE_STALE_TTL:
        return versions

    return None


def get_cached_versions_stale(recipe_key: str) -> list[str] | None:
    """获取缓存的版本列表（即使过期也返回，用于兜底）"""
    _ensure_loaded()
    entry = _cache.get(recipe_key)
    if entry is None:
        return None
    versions = entry.get("versions", [])
    return versions if versions else None


def update_cache(recipe_key: str, versions: list[str]) -> None:
    """更新单个 recipe 的版本缓存"""
    _ensure_loaded()
    with _lock:
        _cache[recipe_key] = {
            "versions": versions,
            "timestamp": time.time(),
        }
        _save_cache(_cache)


# ─── 版本获取 ─────────────────────────────────────────────────────────────────

def fetch_versions_online(recipe) -> list[str] | None:
    """根据 Recipe 的 version_source 声明，在线获取版本列表

    支持的 version_source 类型：
    - github_api：从 GitHub Releases API 获取
    - endoflife：从 endoflife.date API 获取
    - custom_api：从自定义 URL 获取
    - none：不获取版本（如 WireGuard 用 latest）
    """
    source = getattr(recipe, "version_source", "none")
    if source == "none":
        return getattr(recipe, "fallback_versions", ["latest"])

    from core.constants import VERSION_FETCH_TIMEOUT

    if source == "github_api":
        return _fetch_github_versions(recipe, timeout=VERSION_FETCH_TIMEOUT)
    elif source == "endoflife":
        return _fetch_endoflife_versions(recipe, timeout=VERSION_FETCH_TIMEOUT)
    elif source == "custom_api":
        return _fetch_custom_versions(recipe, timeout=VERSION_FETCH_TIMEOUT)

    return None


def _fetch_github_versions(recipe, *, timeout: float) -> list[str] | None:
    """从 GitHub API 获取版本"""
    from core.http import get_json
    repo = getattr(recipe, "version_source_key", "")
    if not repo:
        return None

    from core.constants import GITHUB_API_RELEASES_LIST
    url = GITHUB_API_RELEASES_LIST.format(repo=repo)
    data = get_json(url, timeout=timeout, retries=1)
    if data is None:
        return None

    try:
        versions = [
            r["tag_name"].lstrip("v")
            for r in data
            if not r.get("prerelease") and not r.get("draft")
        ]
        return versions[:8] if versions else None
    except Exception:
        return None


def _fetch_endoflife_versions(recipe, *, timeout: float) -> list[str] | None:
    """从 endoflife.date API 获取版本"""
    from core.http import get_json
    product = getattr(recipe, "version_source_key", "")
    if not product:
        return None

    from core.constants import ENDOFLIFE_API
    url = ENDOFLIFE_API.format(product=product)
    data = get_json(url, timeout=timeout, retries=1)
    if data is None:
        return None

    try:
        versions = [
            item["latest"]
            for item in data
            if not item.get("eol") or item.get("eol") is True
        ]
        return versions[:8] if versions else None
    except Exception:
        return None


def _fetch_custom_versions(recipe, *, timeout: float) -> list[str] | None:
    """从自定义 API 获取版本"""
    from core.http import get_json
    url = getattr(recipe, "version_api_url", "")
    if not url:
        return None

    data = get_json(url, timeout=timeout, retries=1)
    if data is None:
        return None

    try:
        if hasattr(recipe, "parse_versions"):
            return recipe.parse_versions(data)
    except Exception:
        pass
    return None


# ─── 后台刷新 ─────────────────────────────────────────────────────────────────

_refresh_event = threading.Event()


def notify_mirrors_ready() -> None:
    """源管理层初始化完成后调用，通知版本缓存层可以开始刷新"""
    _refresh_event.set()


def refresh_all_background() -> None:
    """后台 daemon 线程：刷新所有 Recipe 的版本缓存

    等待源管理层初始化完成（最多 15s），然后串行刷新每个 Recipe。
    """
    from core.constants import VERSION_CACHE_TTL, VERSION_FETCH_INTERVAL

    if not _refresh_event.wait(timeout=15):
        logger.debug("源管理层初始化超时，使用默认源继续")

    _ensure_loaded()

    from software.registry import all_recipes
    recipes = all_recipes()

    for cls in recipes:
        try:
            recipe_key = cls.key
            entry = _cache.get(recipe_key)
            if entry:
                age = time.time() - entry.get("timestamp", 0)
                if age < VERSION_CACHE_TTL:
                    continue

            instance = cls()
            versions = fetch_versions_online(instance)
            if versions:
                update_cache(recipe_key, versions)
                logger.debug("版本缓存已刷新: %s → %s", recipe_key, versions[:3])
            else:
                logger.debug("版本获取失败，保留旧缓存: %s", recipe_key)

        except Exception as e:
            logger.debug("版本刷新异常 %s: %s", cls.key, e)

        time.sleep(VERSION_FETCH_INTERVAL / 1000.0)
