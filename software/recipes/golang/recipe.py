"""GoRecipe 主类：纯调度，安装骨架复用 VersionedTarballRecipe，仅保留差异化钩子"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from software._shared.versioned_recipe import VersionedTarballRecipe
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    go_bin_dir,
    go_version_dir,
    go_versions_dir,
    version_list,
    download_golang_tarball,
)
from .driver import get_driver


@register
class GoRecipe(VersionedTarballRecipe):
    key: ClassVar[str] = "golang"
    category: ClassVar[str] = "devtools"
    description: ClassVar[str] = "Go 编程语言"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    _error_ns: ClassVar[str] = "golang_error"
    _shim_cmd: ClassVar[str] = "go"
    _bin_dir_snap_key: ClassVar[str] = "go_bin_dir"
    _tmpdir_prefix: ClassVar[str] = "opskit-go-"
    _dir_prefix: ClassVar[str] = "go"
    _tarball_stem: ClassVar[str] = "go{version}"

    def _get_driver(self):
        return get_driver()

    def _versions_dir(self) -> Path:
        return go_versions_dir()

    def _version_dir(self, version: str) -> Path:
        return go_version_dir(version)

    def _bin_dir(self, version: str) -> Path:
        return go_bin_dir(version)

    def _download(self, version: str, dest: Path):
        return download_golang_tarball(version, dest)

    def _load_snapshot(self) -> dict:
        return load_snapshot()

    def _save_snapshot(self, data: dict) -> None:
        save_snapshot(data)

    def _delete_snapshot(self) -> None:
        delete_snapshot()

    def versions(self) -> list[str]:
        return version_list()
