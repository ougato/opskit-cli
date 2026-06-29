"""跨平台共用工具：路径、快照、版本查找、tarball 下载（对齐 golang/common.py）"""
from __future__ import annotations

import platform
from pathlib import Path

from software._shared.snapshot import SnapshotStore
from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_MONGODB_FILE


# ─── 架构映射 ─────────────────────────────────────────────────────────────────

def _mongo_arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def _mongo_arch_darwin() -> str:
    m = platform.machine().lower()
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "x86_64"


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def mongo_versions_dir() -> Path:
    """所有 MongoDB 版本的根目录：~/.opskit/mongodb/"""
    from .constants import MONGO_PRIVATE_SUBDIR
    return Path.home() / MONGO_PRIVATE_SUBDIR


def mongo_version_dir(version: str) -> Path:
    """指定版本的安装目录：~/.opskit/mongodb/mongodb{version}/"""
    return mongo_versions_dir() / f"mongodb{version}"


def mongo_bin_dir(version: str) -> Path:
    """指定版本的 bin 目录：~/.opskit/mongodb/mongodb{version}/bin/"""
    return mongo_version_dir(version) / "bin"


def shim_dir() -> Path:
    """shim 目录：~/.opskit/mongodb/shims/"""
    return mongo_versions_dir() / "shims"


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

_store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_MONGODB_FILE)


def snapshot_path() -> Path:
    return _store.path


def load_snapshot() -> dict:
    return _store.load()


def save_snapshot(data: dict) -> None:
    _store.save(data)


def delete_snapshot() -> None:
    _store.delete()


# ─── tarball 下载 ─────────────────────────────────────────────────────────────

def download_mongodb_tarball(version: str, dest: Path) -> Path:
    """
    赛马下载 MongoDB tarball，复用 core.mirror.download() 机制。
    URL 列表来自 constants.py，国内镜像优先，最后一条作为 fallback。
    """
    import sys
    from core import mirror
    from software.base import InstallError
    from .constants import (
        MONGO_DL_LINUX_URLS,
        MONGO_DL_DARWIN_URLS,
        MONGO_DL_WINDOWS_URLS,
    )

    if sys.platform == "win32":
        url_templates = MONGO_DL_WINDOWS_URLS
        arch = "x86_64"
    elif sys.platform == "darwin":
        url_templates = MONGO_DL_DARWIN_URLS
        arch = _mongo_arch_darwin()
    else:
        url_templates = MONGO_DL_LINUX_URLS
        arch = _mongo_arch()

    urls = [u.format(version=version, arch=arch) for u in url_templates]

    try:
        return mirror.download_file(
            urls=urls,
            dest=dest,
        )
    except Exception as e:
        from core.i18n import t
        raise InstallError(t("software.mongodb_error.download_failed", version=version, error=e)) from e
