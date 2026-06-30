"""NodeRecipe 主类：纯调度，安装骨架复用 VersionedTarballRecipe，仅保留差异化钩子"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import ClassVar

from software._shared.versioned_recipe import VersionedTarballRecipe
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    node_bin_dir,
    node_version_dir,
    node_versions_dir,
    version_list,
    download_nodejs_tarball,
)
from .driver import get_driver


@register
class NodeRecipe(VersionedTarballRecipe):
    key: ClassVar[str] = "nodejs"
    category: ClassVar[str] = "devtools"
    description: ClassVar[str] = "Node.js 运行时"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    _error_ns: ClassVar[str] = "nodejs_error"
    _shim_cmd: ClassVar[str] = "node"
    _bin_dir_snap_key: ClassVar[str] = "node_bin_dir"
    _tmpdir_prefix: ClassVar[str] = "opskit-node-"
    _dir_prefix: ClassVar[str] = "node"
    _tarball_stem: ClassVar[str] = "node-v{version}"

    def _get_driver(self):
        return get_driver()

    def _versions_dir(self) -> Path:
        return node_versions_dir()

    def _version_dir(self, version: str) -> Path:
        return node_version_dir(version)

    def _bin_dir(self, version: str) -> Path:
        return node_bin_dir(version)

    def _download(self, version: str, dest: Path):
        return download_nodejs_tarball(version, dest)

    def _load_snapshot(self) -> dict:
        return load_snapshot()

    def _save_snapshot(self, data: dict) -> None:
        save_snapshot(data)

    def _delete_snapshot(self) -> None:
        delete_snapshot()

    def versions(self) -> list[str]:
        return version_list()

    def _tarball_ext(self) -> str:
        if sys.platform == "win32":
            return ".zip"
        if sys.platform == "darwin":
            return ".tar.gz"
        return ".tar.xz"

    # Windows：node.exe 在版本根目录；Linux/macOS：bin/ 子目录
    def _switch_bin_dir(self, version: str) -> str:
        if sys.platform == "win32":
            return str(node_version_dir(version))
        return str(node_bin_dir(version))

    def system_version(self) -> str | None:
        node_cmd = shutil.which("node")
        if node_cmd:
            try:
                import subprocess
                r = subprocess.run([node_cmd, "--version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if line.startswith("v"):
                    return line[1:]
            except Exception:
                pass
        return None
