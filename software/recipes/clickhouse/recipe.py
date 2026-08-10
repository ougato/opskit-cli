"""ClickHouse recipe."""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from software._shared.versioned_recipe import VersionedTarballRecipe
from software.registry import register
from .common import (
    clickhouse_bin_dir,
    clickhouse_version_dir,
    clickhouse_versions_dir,
    delete_snapshot,
    download_clickhouse_tarball,
    fetch_versions,
    load_snapshot,
    parse_release_versions,
    save_snapshot,
)
from .constants import CLICKHOUSE_RELEASES_API_URL, CLICKHOUSE_VERSIONS_FALLBACK
from .driver import get_driver


@register
class ClickHouseRecipe(VersionedTarballRecipe):
    key: ClassVar[str] = "clickhouse"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "ClickHouse 列式数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True
    version_source: ClassVar[str] = "custom_api"
    version_api_url: ClassVar[str] = CLICKHOUSE_RELEASES_API_URL

    _error_ns: ClassVar[str] = "clickhouse_error"
    _shim_cmd: ClassVar[str] = "clickhouse"
    _bin_dir_snap_key: ClassVar[str] = "clickhouse_bin_dir"
    _tmpdir_prefix: ClassVar[str] = "opskit-clickhouse-"
    _dir_prefix: ClassVar[str] = "clickhouse"
    _tarball_stem: ClassVar[str] = "clickhouse-common-static-{version}"

    def _get_driver(self):
        return get_driver()

    def _versions_dir(self) -> Path:
        return clickhouse_versions_dir()

    def _version_dir(self, version: str) -> Path:
        return clickhouse_version_dir(version)

    def _bin_dir(self, version: str) -> Path:
        return clickhouse_bin_dir(version)

    def _download(self, version: str, dest: Path):
        return download_clickhouse_tarball(version, dest)

    def _load_snapshot(self) -> dict:
        return load_snapshot()

    def _save_snapshot(self, data: dict) -> None:
        save_snapshot(data)

    def _delete_snapshot(self) -> None:
        delete_snapshot()

    def _sort_key(self, version: str) -> list[int]:
        return [int(part) for part in version.split(".") if part.isdigit()]

    def _tarball_ext(self) -> str:
        return ".tgz"

    def parse_versions(self, data: object) -> list[str]:
        return parse_release_versions(data)

    def versions(self) -> list[str]:
        from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache

        key = "clickhouse"
        cached = get_cached_versions(key)
        if cached:
            return cached
        try:
            versions = fetch_versions()
            if versions:
                update_cache(key, versions)
                return versions
        except Exception:
            pass
        stale = get_cached_versions_stale(key)
        if stale:
            return stale
        return list(CLICKHOUSE_VERSIONS_FALLBACK)
