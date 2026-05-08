"""跨平台共用工具：路径、快照、版本查找、tarball 下载（对齐 golang/common.py）"""
from __future__ import annotations

import json
import platform
from pathlib import Path


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

def snapshot_path() -> Path:
    from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_MONGODB_FILE
    return Path.home() / SNAPSHOT_SUBDIR / SNAPSHOT_MONGODB_FILE


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
