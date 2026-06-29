"""跨平台共用工具：路径、快照、版本查找、版本列表、tarball 赛马下载"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

from software._shared.snapshot import SnapshotStore
from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_JAVA_FILE

from software.base import InstallError


# ─── 架构映射 ─────────────────────────────────────────────────────────────────

def _java_arch() -> str:
    """将 Python platform.machine() 映射为 Adoptium API 架构字符串"""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m.startswith("armv"):
        return "arm"
    if m in ("i386", "i686", "x86"):
        return "x86"
    return "x64"


def _java_os() -> str:
    """将 sys.platform 映射为 Adoptium API os 字符串"""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def java_versions_dir() -> Path:
    """所有 JDK 版本的根目录：~/.opskit/java/"""
    from .constants import JAVA_PRIVATE_SUBDIR
    return Path.home() / JAVA_PRIVATE_SUBDIR


def java_version_dir(version: str) -> Path:
    """
    指定版本的安装目录：~/.opskit/java/jdk{version}/
    version 格式如 '21.0.11+10' → 目录名 'jdk21.0.11+10'
    文件系统用 + 替换为 _ 避免路径问题
    """
    safe = version.replace("+", "_")
    return java_versions_dir() / f"jdk{safe}"


def java_bin_dir(version: str) -> Path:
    """指定版本的 bin 目录：~/.opskit/java/jdk{version}/bin/"""
    return java_version_dir(version) / "bin"


def shim_dir() -> Path:
    """shim 目录：~/.opskit/java/shims/"""
    return java_versions_dir() / "shims"


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

_store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_JAVA_FILE)


def snapshot_path() -> Path:
    return _store.path


def load_snapshot() -> dict:
    return _store.load()


def save_snapshot(data: dict) -> None:
    _store.save(data)


def delete_snapshot() -> None:
    _store.delete()


# ─── 版本列表 ─────────────────────────────────────────────────────────────────

def version_list() -> list[str]:
    """
    获取可安装 JDK 版本列表，四级降级：
    0. 本地缓存（未过期）
    1. Adoptium API v3 并发查询（各 LTS major 同时请求）
    2. endoflife.date/api/java.json
    3. 过期缓存兜底
    4. 硬编码 fallback
    返回版本号字符串列表（如 '21.0.11+10'），降序排列（大版本优先）。
    """
    from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.constants import TIMEOUT_VERSION_FETCH
    from .constants import JAVA_RELEASES_API, JAVA_ASSETS_API, JAVA_VERSIONS_FALLBACK

    _KEY = "java"
    cached = get_cached_versions(_KEY)
    if cached and any(v[0].isdigit() for v in cached if v):
        return cached

    os_str = _java_os()
    arch   = _java_arch()
    raw: list[str] = []

    try:
        import httpx

        # Step 1: 获取 LTS 列表
        resp = httpx.get(JAVA_RELEASES_API, timeout=TIMEOUT_VERSION_FETCH, follow_redirects=True)
        lts_list: list[int] = []
        if resp.status_code == 200:
            lts_list = resp.json().get("available_lts_releases", [])

        if lts_list:
            # Step 2: 并发查询每个 LTS major
            def _fetch_major(major: int) -> str:
                try:
                    url = JAVA_ASSETS_API.format(major=major, os=os_str, arch=arch)
                    r2 = httpx.get(url, timeout=TIMEOUT_VERSION_FETCH, follow_redirects=True)
                    if r2.status_code == 200:
                        data = r2.json()
                        if data:
                            semver = data[0].get("version", {}).get("openjdk_version", "")
                            semver = semver.replace("-LTS", "").replace("-EA", "")
                            return semver
                except Exception:
                    pass
                return ""

            with ThreadPoolExecutor(max_workers=min(len(lts_list), 8)) as pool:
                futures = {pool.submit(_fetch_major, m): m for m in lts_list}
                for f in as_completed(futures, timeout=TIMEOUT_VERSION_FETCH + 2):
                    ver = f.result()
                    if ver:
                        raw.append(ver)
    except Exception:
        pass

    if not raw:
        try:
            import httpx
            resp = httpx.get(
                "https://endoflife.date/api/java.json",
                timeout=TIMEOUT_VERSION_FETCH,
            )
            if resp.status_code == 200:
                for item in resp.json():
                    v = item.get("latest", "")
                    if v:
                        raw.append(v)
        except Exception:
            pass

    def _ver_key(v: str) -> list[int]:
        base = v.split("+")[0]
        return [int(x) for x in base.split(".") if x.isdigit()]

    if raw:
        raw.sort(key=_ver_key, reverse=True)
        update_cache(_KEY, raw)
        return raw

    stale = get_cached_versions_stale(_KEY)
    if stale and any(v[0].isdigit() for v in stale if v):
        return stale

    raw = list(JAVA_VERSIONS_FALLBACK)
    raw.sort(key=_ver_key, reverse=True)
    return raw


# ─── 获取版本下载 URL（Adoptium API 直接返回，ghproxy 加速）────────────────────

def _build_filename(version: str, arch: str, os_str: str) -> str:
    """\u6839据版本号构造 Adoptium 标准文件名，+ 替换为 _"""
    major = int(version.split(".")[0])
    base, build = version.split("+")
    parts = base.split(".")
    ver_clean = f"{parts[0]}.{parts[1]}.{parts[2]}"
    ext = "zip" if sys.platform == "win32" else "tar.gz"
    return f"OpenJDK{major}U-jdk_{arch}_{os_str}_hotspot_{ver_clean}_{build}.{ext}"


def get_download_info(version: str) -> tuple[str, str, str]:
    """
    返回 (cn_mirror_url, raw_github_url, filename)。
    优先级：清华镜像 → ghproxy → GitHub 官方直连。
    尽量不调 API，按规则直接构造 URL。
    """
    from .constants import GHPROXY_PREFIX, JAVA_CN_MIRRORS

    os_str = _java_os()
    arch   = _java_arch()
    major  = int(version.split(".")[0])

    filename = _build_filename(version, arch, os_str)

    # 构造清华镜像 URL（filename 中 + 已是 _）
    tuna_url = f"{JAVA_CN_MIRRORS[0]}/{major}/jdk/{arch}/{os_str}/{filename}"

    # GitHub 原始 URL
    tag = version.replace("+", "%2B")
    raw_url = f"https://github.com/adoptium/temurin{major}-binaries/releases/download/jdk-{tag}/{filename}"
    ghproxy_url = GHPROXY_PREFIX + raw_url

    return tuna_url, raw_url, filename


# ─── 赛马下载 JDK tarball ─────────────────────────────────────────────────────

def download_java_tarball(
    version: str,
    dest: Path,
    progress_callback=None,
) -> Path:
    """
    赛马下载 JDK tarball（tar.gz / zip）。

    下载优先级：
    1. 清华镜像（国内首选）
    2. ghproxy 加速 GitHub Releases
    3. GitHub Releases 官方直连（最终兜底）
    """
    from core import mirror
    from .constants import GHPROXY_PREFIX, JAVA_CN_MIRRORS

    os_str = _java_os()
    arch   = _java_arch()
    major  = int(version.split(".")[0])
    filename = _build_filename(version, arch, os_str)

    tag = version.replace("+", "%2B")
    raw_url = f"https://github.com/adoptium/temurin{major}-binaries/releases/download/jdk-{tag}/{filename}"
    ghproxy_url = GHPROXY_PREFIX + raw_url

    # 所有国内镜像 URL（按优先级）
    cn_urls = [
        f"{base}/{major}/jdk/{arch}/{os_str}/{filename}"
        for base in JAVA_CN_MIRRORS
    ]

    cache_path = mirror.get_download_cache_path("java", version.replace("+", "_"), filename)

    try:
        return mirror.download_file(
            urls=cn_urls + [ghproxy_url, raw_url],
            dest=dest,
            cache_path=cache_path,
            progress_callback=progress_callback,
        )
    except Exception as e:
        from core.i18n import t
        raise InstallError(t("software.java_error.download_failed", version=version, error=e)) from e
