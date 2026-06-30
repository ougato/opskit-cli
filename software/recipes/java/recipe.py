"""JavaRecipe 主类：纯调度，安装骨架复用 VersionedTarballRecipe，仅保留差异化钩子"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from software._shared.versioned_recipe import VersionedTarballRecipe
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    java_bin_dir,
    java_version_dir,
    java_versions_dir,
    version_list,
    download_java_tarball,
)
from .driver import get_driver


@register
class JavaRecipe(VersionedTarballRecipe):
    key: ClassVar[str] = "java"
    category: ClassVar[str] = "devtools"
    description: ClassVar[str] = "Java JDK"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    _error_ns: ClassVar[str] = "java_error"
    _shim_cmd: ClassVar[str] = "java"
    _bin_dir_snap_key: ClassVar[str] = "java_bin_dir"
    _tmpdir_prefix: ClassVar[str] = "opskit-java-"
    _dir_prefix: ClassVar[str] = "jdk"
    _tarball_stem: ClassVar[str] = "jdk-{version}"

    def _get_driver(self):
        return get_driver()

    def _versions_dir(self) -> Path:
        return java_versions_dir()

    def _version_dir(self, version: str) -> Path:
        return java_version_dir(version)

    def _bin_dir(self, version: str) -> Path:
        return java_bin_dir(version)

    def _download(self, version: str, dest: Path):
        return download_java_tarball(version, dest)

    def _load_snapshot(self) -> dict:
        return load_snapshot()

    def _save_snapshot(self, data: dict) -> None:
        save_snapshot(data)

    def _delete_snapshot(self) -> None:
        delete_snapshot()

    def versions(self) -> list[str]:
        return version_list()

    # jdk21.0.11_10 → 21.0.11+10：目录名用 _ 编码 +
    def _decode_version(self, raw: str) -> str:
        return raw.replace("_", "+")

    def _sort_key(self, version: str) -> list[int]:
        return [int(x) for x in version.split("+")[0].split(".") if x.isdigit()]

    def system_version(self) -> str | None:
        java_cmd = shutil.which("java")
        if java_cmd:
            try:
                import subprocess
                r = subprocess.run(
                    [java_cmd, "-version"],
                    capture_output=True, text=True, timeout=5,
                )
                line = (r.stderr or r.stdout).strip().splitlines()[0]
                if '"' in line:
                    return line.split('"')[1]
            except Exception:
                pass
        return None
