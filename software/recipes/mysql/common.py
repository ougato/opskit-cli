"""跨平台共用工具：路径、快照、版本查找、tarball 下载（对齐 mongodb/common.py）"""
from __future__ import annotations

import platform
from pathlib import Path

from software._shared.snapshot import SnapshotStore
from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_MYSQL_FILE


# ─── 架构映射 ─────────────────────────────────────────────────────────────────

def _mysql_arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def _mysql_arch_darwin() -> str:
    m = platform.machine().lower()
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "x86_64"


def _mysql_macos_ver(version: str) -> str:
    """
    根据 MySQL 版本号返回对应的 macOS 标识数字。
    实测规律（2025-05）：
      - 8.0.x <= 8.0.40  → macos14
      - 8.0.41+          → macos15
      - 8.4.x <= 8.4.3   → macos14
      - 8.4.4+           → macos15
      - 9.x              → macos15
    """
    try:
        parts = [int(x) for x in version.split(".")]
        major = parts[0] if len(parts) > 0 else 0
        minor = parts[1] if len(parts) > 1 else 0
        patch = parts[2] if len(parts) > 2 else 0
        if major >= 9:
            return "15"
        if major == 8 and minor == 4:
            return "15" if patch >= 4 else "14"
        if major == 8 and minor == 0:
            return "15" if patch >= 41 else "14"
    except Exception:
        pass
    return "14"


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def mysql_versions_dir() -> Path:
    """所有 MySQL 版本的根目录：~/.opskit/mysql/"""
    from .constants import MYSQL_PRIVATE_SUBDIR
    return Path.home() / MYSQL_PRIVATE_SUBDIR


def mysql_version_dir(version: str) -> Path:
    """指定版本的安装目录：~/.opskit/mysql/mysql{version}/"""
    return mysql_versions_dir() / f"mysql{version}"


def mysql_bin_dir(version: str) -> Path:
    """指定版本的 bin 目录：~/.opskit/mysql/mysql{version}/bin/"""
    return mysql_version_dir(version) / "bin"


def shim_dir() -> Path:
    """shim 目录：~/.opskit/mysql/shims/"""
    return mysql_versions_dir() / "shims"


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

_store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_MYSQL_FILE)


def snapshot_path() -> Path:
    return _store.path


def load_snapshot() -> dict:
    return _store.load()


def save_snapshot(data: dict) -> None:
    _store.save(data)


def delete_snapshot() -> None:
    _store.delete()


# ─── 系统信息 ────────────────────────────────────────────────────────────────

def get_distro_codename() -> str:
    """读取 /etc/os-release 的 VERSION_CODENAME，失败返回空字符串"""
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == "VERSION_CODENAME":
                    return v.strip().strip('"').lower()
    except Exception:
        pass
    return ""


# ─── tarball 解压 ─────────────────────────────────────────────────────────────

def extract_tarball(tarball, dest: Path, version: str) -> None:
    """
    将 tar（.tar.xz 或 .tar.gz）解压到 dest，自动剥除顶层目录。
    保留文件可执行位。失败时抛出 InstallError。
    """
    import shutil
    import tarfile as _tarfile
    from software.base import InstallError
    from core.i18n import t
    mode = "r:gz" if str(tarball).endswith(".tar.gz") else "r:xz"
    try:
        with _tarfile.open(str(tarball), mode) as tf:
            for member in tf.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                target = dest / parts[1]
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tf.extractfile(member) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    if member.mode & 0o111:
                        target.chmod(target.stat().st_mode | 0o111)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(t("software.mysql_error.extract_failed", version=version, error=e)) from e


# ─── detect 共用逻辑 ──────────────────────────────────────────────────────────

def detect_mysql_version(active_bin_name: str = "mysql") -> str | None:
    """
    检测当前活跃 MySQL 版本：先查快照，再用 shutil.which + --version 解析。
    active_bin_name：Linux/macOS 传 'mysql'，Windows 传 'mysql.exe'。
    """
    import shutil
    import subprocess
    snap = load_snapshot()
    active = snap.get("active_version")
    if active:
        candidate = mysql_bin_dir(active) / active_bin_name
        if candidate.exists():
            return active
    mysql_cmd = shutil.which("mysql")
    if mysql_cmd:
        try:
            r = subprocess.run(
                [mysql_cmd, "--version"], capture_output=True, text=True, timeout=5
            )
            for part in r.stdout.strip().split():
                p = part.lstrip("v") if part.startswith("v") else part
                if p and p[0].isdigit():
                    return p.split("-")[0].rstrip(",")
        except Exception:
            pass
    return None


# ─── tarball 下载 ─────────────────────────────────────────────────────────────

def download_mysql_tarball(
    version: str,
    dest: Path,
    progress_callback=None,
) -> Path:
    """
    下载 MySQL tarball，使用 mirror.download_file()（Sequential-with-Probe 策略）。
    HEAD 探针排序 + 逐源流式下载 + Range 断点续传 + 停滞超时，无总时长限制。
    """
    import sys
    from core import mirror
    from core.i18n import t
    from software.base import InstallError
    from .constants import (
        MYSQL_DL_LINUX_URLS,
        MYSQL_DL_DARWIN_URLS,
        MYSQL_DL_WINDOWS_URLS,
        MYSQL_LINUX_GLIBC_OLD, MYSQL_LINUX_GLIBC_17, MYSQL_LINUX_GLIBC_NEW,
        MYSQL_LINUX_EXT_OLD, MYSQL_LINUX_EXT_NEW,
        MYSQL_LINUX_SUFFIX_MINIMAL,
    )

    parts = version.split(".")
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version

    if sys.platform == "win32":
        url_templates = MYSQL_DL_WINDOWS_URLS
        urls = [u.format(version=version, major_minor=major_minor) for u in url_templates]
    elif sys.platform == "darwin":
        arch = _mysql_arch_darwin()
        macos_ver = _mysql_macos_ver(version)
        url_templates = MYSQL_DL_DARWIN_URLS
        urls = [u.format(version=version, major_minor=major_minor, macos_ver=macos_ver, arch=arch) for u in url_templates]
    else:
        arch = _mysql_arch()
        try:
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            major, minor = 99, 0
        url_templates = MYSQL_DL_LINUX_URLS
        if major < 8:
            # 5.x：无 minimal，用 glibc2.12 完整包 .tar.gz
            urls = [u.format(version=version, major_minor=major_minor, arch=arch)
                    .replace(MYSQL_LINUX_GLIBC_NEW, MYSQL_LINUX_GLIBC_OLD)
                    .replace(MYSQL_LINUX_EXT_NEW, MYSQL_LINUX_EXT_OLD)
                    for u in url_templates]
        elif major == 8 and minor < 4:
            # 8.0-8.3：glibc2.17-minimal.tar.xz（bundled 私有库，无系统依赖）
            urls = [u.format(version=version, major_minor=major_minor, arch=arch)
                    .replace(MYSQL_LINUX_GLIBC_NEW, MYSQL_LINUX_GLIBC_17)
                    .replace(".tar.xz", f"{MYSQL_LINUX_SUFFIX_MINIMAL}.tar.xz")
                    for u in url_templates]
        else:
            # 8.4+ / 9.x：glibc2.28-minimal.tar.xz（bundled 私有库，无系统依赖）
            urls = [u.format(version=version, major_minor=major_minor, arch=arch)
                    .replace(".tar.xz", f"{MYSQL_LINUX_SUFFIX_MINIMAL}.tar.xz")
                    for u in url_templates]

    cache_path = mirror.get_download_cache_path(
        "mysql", version, urls[0].rsplit("/", 1)[-1].split("?")[0]
    )

    try:
        return mirror.download_file(
            urls=urls,
            dest=dest,
            cache_path=cache_path,
            progress_callback=progress_callback,
        )
    except Exception as e:
        raise InstallError(t("software.mysql_error.download_failed", version=version, error=e)) from e
