"""MongoDBRecipe 主类：纯调度，安装骨架复用 VersionedTarballRecipe，仅保留差异化钩子"""
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
    mongo_bin_dir,
    mongo_version_dir,
    mongo_versions_dir,
    download_mongodb_tarball,
)
from .constants import (
    MONGO_VERSIONS_FALLBACK,
    MONGO_VERSIONS_API_URL,
)
from .driver import get_driver


@register
class MongoDBRecipe(VersionedTarballRecipe):
    key: ClassVar[str] = "mongodb"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "MongoDB 文档型数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    _error_ns: ClassVar[str] = "mongodb_error"
    _shim_cmd: ClassVar[str] = "mongod"
    _bin_dir_snap_key: ClassVar[str] = "mongod_bin_dir"
    _tmpdir_prefix: ClassVar[str] = "opskit-mongo-"
    _dir_prefix: ClassVar[str] = "mongodb"
    _tarball_stem: ClassVar[str] = "mongodb-{version}"

    def _get_driver(self):
        return get_driver()

    def _versions_dir(self) -> Path:
        return mongo_versions_dir()

    def _version_dir(self, version: str) -> Path:
        return mongo_version_dir(version)

    def _bin_dir(self, version: str) -> Path:
        return mongo_bin_dir(version)

    def _download(self, version: str, dest: Path):
        return download_mongodb_tarball(version, dest)

    def _load_snapshot(self) -> dict:
        return load_snapshot()

    def _save_snapshot(self, data: dict) -> None:
        save_snapshot(data)

    def _delete_snapshot(self) -> None:
        delete_snapshot()

    def _tarball_ext(self) -> str:
        return ".zip" if sys.platform == "win32" else ".tgz"

    def versions(self) -> list[str]:
        from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
        from core.constants import TIMEOUT_VERSION_FETCH
        _KEY = "mongodb"
        cached = get_cached_versions(_KEY)
        if cached and any(v[0].isdigit() for v in cached if v):
            return cached
        try:
            import httpx
            resp = httpx.get(MONGO_VERSIONS_API_URL, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                vers = [item.get("latest", "") for item in resp.json()
                        if item.get("latest", "")]
                vers = [v for v in vers if v and v[0].isdigit()]
                if vers:
                    update_cache(_KEY, vers)
                    return vers
        except Exception:
            pass
        stale = get_cached_versions_stale(_KEY)
        if stale and any(v[0].isdigit() for v in stale if v):
            return stale
        return list(MONGO_VERSIONS_FALLBACK)
