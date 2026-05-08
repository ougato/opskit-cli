"""跨平台共用工具：路径、快照、版本查找、版本列表、tarball 赛马下载"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

from software.base import InstallError


# ─── 架构映射 ─────────────────────────────────────────────────────────────────

def _node_arch() -> str:
    """将 Python platform.machine() 映射为 Node.js 架构字符串"""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    if m.startswith("armv"):
        return "armv7l"
    if m in ("i386", "i686", "x86"):
        return "x86"
    return "x64"


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def node_versions_dir() -> Path:
    """所有 Node 版本的根目录：~/.opskit/nodejs/"""
    from .constants import NODEJS_PRIVATE_SUBDIR
    return Path.home() / NODEJS_PRIVATE_SUBDIR


def node_version_dir(version: str) -> Path:
    """指定版本的安装目录：~/.opskit/nodejs/nodeX.Y.Z/"""
    return node_versions_dir() / f"node{version}"


def node_bin_dir(version: str) -> Path:
    """指定版本的 bin 目录：~/.opskit/nodejs/nodeX.Y.Z/bin/"""
    return node_version_dir(version) / "bin"


def shim_dir() -> Path:
    """shim 目录：~/.opskit/nodejs/shims/"""
    return node_versions_dir() / "shims"


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

def snapshot_path() -> Path:
    from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_NODEJS_FILE
    return Path.home() / SNAPSHOT_SUBDIR / SNAPSHOT_NODEJS_FILE


def load_snapshot() -> dict:
    p = snapshot_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_snapshot(data: dict) -> None:
    p = snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_snapshot() -> None:
    p = snapshot_path()
    if p.exists():
        p.unlink()


# ─── 版本列表 ─────────────────────────────────────────────────────────────────

def version_list() -> list[str]:
    """
    获取可安装 Node.js 版本列表，只保留 LTS + 最新稳定版，四级降级：
    0. 本地缓存（未过期）
    1. nodejs.org/dist/index.json（官方 JSON API）
    2. endoflife.date/api/nodejs.json
    3. 过期缓存兜底
    4. 硬编码 fallback
    返回版本号字符串列表，降序排列。
    """
    from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
    from core.constants import TIMEOUT_VERSION_FETCH
    from .constants import NODEJS_VERSIONS_API, NODEJS_EOL_API, NODEJS_VERSIONS_FALLBACK

    _KEY = "nodejs"
    cached = get_cached_versions(_KEY)
    if cached and any(v[0].isdigit() for v in cached if v):
        return cached

    raw: list[str] = []

    try:
        import httpx
        resp = httpx.get(NODEJS_VERSIONS_API, timeout=TIMEOUT_VERSION_FETCH, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            seen: set[str] = set()
            for entry in data:
                v = entry.get("version", "")
                if v.startswith("v"):
                    v = v[1:]
                # 只保留 LTS 版本（偶数主版本）或最新稳定版
                try:
                    major = int(v.split(".")[0])
                    is_lts = entry.get("lts", False) not in (False, None, "")
                    if not is_lts and major % 2 != 0:
                        continue
                except Exception:
                    pass
                if v and v not in seen:
                    seen.add(v)
                    raw.append(v)
    except Exception:
        pass

    if not raw:
        try:
            import httpx
            resp = httpx.get(NODEJS_EOL_API, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                data = resp.json()
                for item in data:
                    v = item.get("latest", "")
                    if v:
                        raw.append(v)
        except Exception:
            pass

    def _ver_key(v: str) -> list[int]:
        return [int(x) for x in v.split(".") if x.isdigit()]

    if raw:
        raw.sort(key=_ver_key, reverse=True)
        update_cache(_KEY, raw)
        return raw

    stale = get_cached_versions_stale(_KEY)
    if stale and any(v[0].isdigit() for v in stale if v):
        return stale

    raw = list(NODEJS_VERSIONS_FALLBACK)
    raw.sort(key=_ver_key, reverse=True)
    return raw


# ─── 赛马下载 Node.js tarball ─────────────────────────────────────────────────

def download_nodejs_tarball(
    version: str,
    dest: Path,
    progress_callback=None,
) -> Path:
    """
    赛马下载 Node.js tarball（tar.xz / tar.gz / zip），复用 core.mirror.download() 的
    HEAD 探针 + 多源并发赛马机制。

    URL 列表来自 constants.py，按优先级排列（国内镜像 → 官方），
    最后一个 URL 作为 fallback 兜底。
    """
    from core import mirror
    from .constants import (
        NODEJS_DL_LINUX_URLS,
        NODEJS_DL_DARWIN_URLS,
        NODEJS_DL_WINDOWS_URLS,
    )

    arch = _node_arch()

    if sys.platform == "win32":
        url_templates = NODEJS_DL_WINDOWS_URLS
    elif sys.platform == "darwin":
        url_templates = NODEJS_DL_DARWIN_URLS
    else:
        url_templates = NODEJS_DL_LINUX_URLS

    urls = [u.format(version=version, arch=arch) for u in url_templates]

    filename = urls[0].rsplit("/", 1)[-1].split("?")[0]
    cache_path = mirror.get_download_cache_path("nodejs", version, filename)

    try:
        return mirror.download_file(
            urls=urls,
            dest=dest,
            cache_path=cache_path,
            progress_callback=progress_callback,
        )
    except Exception as e:
        from core.i18n import t
        raise InstallError(t("software.nodejs_error.download_failed", version=version, error=e)) from e
