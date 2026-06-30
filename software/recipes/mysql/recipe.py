"""MySQLRecipe 主类：纯调度，安装骨架复用 VersionedTarballRecipe，仅保留差异化钩子"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

from software._shared.versioned_recipe import VersionedTarballRecipe
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    mysql_bin_dir,
    mysql_version_dir,
    mysql_versions_dir,
    download_mysql_tarball,
)
from .constants import (
    MYSQL_VERSIONS_FALLBACK,
    MYSQL_VERSIONS_API_URL,
    MYSQL_NO_5X_CODENAMES,
)
from .driver import get_driver


def _dedup_versions_by_series(versions: list[str]) -> list[str]:
    """
    精简版本列表，避免已下架版本出现：
    - Innovation 系列（major >= 9）：只保留版本号最高的一个（Oracle 只保留最新 Innovation）
    - LTS 系列（8.x）：每个 major.minor 只保留最新版
    - 5.x：每个 major.minor 只保留最新版
    """
    def _ver_tuple(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0,)

    innovation: list[str] = []
    lts: list[str] = []
    old: list[str] = []

    for v in versions:
        try:
            major = int(v.split(".")[0])
        except (ValueError, IndexError):
            continue
        if major >= 9:
            innovation.append(v)
        elif major >= 8:
            lts.append(v)
        else:
            old.append(v)

    result: list[str] = []

    # Innovation：只取版本号最大的一个
    if innovation:
        result.append(max(innovation, key=_ver_tuple))

    # LTS：每个 major.minor 取最新
    seen_lts: set[str] = set()
    for v in sorted(lts, key=_ver_tuple, reverse=True):
        series = ".".join(v.split(".")[:2])
        if series not in seen_lts:
            seen_lts.add(series)
            result.append(v)

    # 5.x 等旧版：每个 major.minor 取最新
    seen_old: set[str] = set()
    for v in sorted(old, key=_ver_tuple, reverse=True):
        series = ".".join(v.split(".")[:2])
        if series not in seen_old:
            seen_old.add(series)
            result.append(v)

    return result


def _filter_versions_by_platform(versions: list[str]) -> list[str]:
    """
    按平台静态过滤版本列表，无网络探针：
    - Windows：过滤掉 5.5.x 及以下（官方无 Windows 包）
    - macOS：过滤掉全部 5.x（官方无 macOS 包）
    - Linux 新系统（bookworm/jammy+）：过滤掉全部 5.x
    - Linux 旧系统：原样返回
    """
    import sys
    from .common import get_distro_codename
    if sys.platform == "win32":
        return [v for v in versions if not v.startswith(("5.5.", "5.4.", "5.3.", "5.2.", "5.1.", "5.0."))]
    if sys.platform == "darwin":
        return [v for v in versions if not v.startswith(("5.", "4.", "3."))]
    if get_distro_codename() in MYSQL_NO_5X_CODENAMES:
        return [v for v in versions if not v.startswith(("5.", "4.", "3."))]
    return versions




@register
class MySQLRecipe(VersionedTarballRecipe):
    key: ClassVar[str] = "mysql"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "MySQL 关系型数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    _error_ns: ClassVar[str] = "mysql_error"
    _shim_cmd: ClassVar[str] = "mysql"
    _bin_dir_snap_key: ClassVar[str] = "mysql_bin_dir"
    _tmpdir_prefix: ClassVar[str] = "opskit-mysql-"
    _dir_prefix: ClassVar[str] = "mysql"
    _tarball_stem: ClassVar[str] = "mysql-{version}"

    def _get_driver(self):
        return get_driver()

    def _versions_dir(self) -> Path:
        return mysql_versions_dir()

    def _version_dir(self, version: str) -> Path:
        return mysql_version_dir(version)

    def _bin_dir(self, version: str) -> Path:
        return mysql_bin_dir(version)

    def _download(self, version: str, dest: Path):
        return download_mysql_tarball(version, dest)

    def _load_snapshot(self) -> dict:
        return load_snapshot()

    def _save_snapshot(self, data: dict) -> None:
        save_snapshot(data)

    def _delete_snapshot(self) -> None:
        delete_snapshot()

    def _tarball_ext(self) -> str:
        if sys.platform == "win32":
            return ".zip"
        return ".tar.gz" if sys.platform == "darwin" else ".tar.xz"

    def versions(self) -> list[str]:
        from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
        from core.constants import TIMEOUT_VERSION_FETCH
        _KEY = "mysql"
        cached = get_cached_versions(_KEY)
        if cached and any(v[0].isdigit() for v in cached if v):
            return _filter_versions_by_platform(_dedup_versions_by_series(cached))
        try:
            import httpx
            resp = httpx.get(MYSQL_VERSIONS_API_URL, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                vers = [item.get("latest", "") for item in resp.json()
                        if item.get("latest", "")]
                vers = [v for v in vers if v and v[0].isdigit()]
                if vers:
                    vers = _dedup_versions_by_series(vers)
                    vers = _filter_versions_by_platform(vers)
                if vers:
                    update_cache(_KEY, vers)
                    return vers
        except Exception:
            pass
        stale = get_cached_versions_stale(_KEY)
        if stale and any(v[0].isdigit() for v in stale if v):
            return _filter_versions_by_platform(_dedup_versions_by_series(stale))
        return list(MYSQL_VERSIONS_FALLBACK)
