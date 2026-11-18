"""跨平台探测 — OS / 包管理器 / init 系统 / 架构 / 预检"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class IssueLevel(str, Enum):
    WARN = "warn"
    ERROR = "error"


@dataclass
class Issue:
    level: IssueLevel
    message: str
    suggestion: str = ""
    code: str = ""  # 稳定标识：non_root / no_pkg_manager / low_disk


@dataclass
class PlatformInfo:
    os_type: str          # 'linux' / 'windows' / 'darwin'
    os_name: str          # 'ubuntu' / 'centos' / 'windows' / 'macos' ...
    os_version: str       # '22.04' / '10' / '13.0' ...
    arch: str             # 'x86_64' / 'aarch64' / 'arm64' / 'armv7' ...
    pkg_manager: str      # 'apt' / 'yum' / 'dnf' / 'apk' / 'brew' / 'choco' / 'winget' / ''
    init_system: str      # 'systemd' / 'sysvinit' / 'openrc' / 'launchd' / 'windows' / ''
    is_root: bool         # 是否具有 root / admin 权限
    python_version: str   # '3.11.2'
    disk_free_bytes: int | None  # 检测时工作目录所在磁盘的快照；None 表示无法读取


# ─── 缓存（进程生命周期内只检测一次）────────────────────────────────────────

_cached: PlatformInfo | None = None


def get_platform() -> PlatformInfo:
    """获取当前平台信息（带缓存）"""
    global _cached
    if _cached is None:
        _cached = _detect()
    return _cached


def _detect() -> PlatformInfo:
    os_type = _os_type()
    os_name, os_version = _os_name_version(os_type)
    arch = _arch()
    pkg_manager = _pkg_manager(os_type, os_name)
    init_system = _init_system(os_type)
    is_root = _is_root(os_type)
    python_version = platform.python_version()
    disk_free_bytes = _disk_free()

    return PlatformInfo(
        os_type=os_type,
        os_name=os_name,
        os_version=os_version,
        arch=arch,
        pkg_manager=pkg_manager,
        init_system=init_system,
        is_root=is_root,
        python_version=python_version,
        disk_free_bytes=disk_free_bytes,
    )


def _os_type() -> str:
    p = sys.platform
    if p.startswith("linux"):
        return "linux"
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "darwin"
    return p


def _os_name_version(os_type: str) -> tuple[str, str]:
    if os_type == "windows":
        ver = platform.version()
        release = platform.release()
        return "windows", release or ver
    if os_type == "darwin":
        mac_ver = platform.mac_ver()[0]
        return "macos", mac_ver
    # Linux — 读 /etc/os-release
    try:
        data: dict[str, str] = {}
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, _, v = line.partition("=")
                    data[k] = v.strip('"')
        name = data.get("ID", "linux").lower()
        version = data.get("VERSION_ID", "")
        return name, version
    except FileNotFoundError:
        return "linux", ""


def _arch() -> str:
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "armv7l": "armv7",
        "armv6l": "armv6",
        "i386": "x86",
        "i686": "x86",
    }
    return mapping.get(machine, machine)


def _pkg_manager(os_type: str, os_name: str) -> str:
    if os_type == "darwin":
        return "brew" if shutil.which("brew") else ""
    if os_type == "windows":
        if sys.platform == "win32":
            if shutil.which("choco"):
                return "choco"
            if shutil.which("winget"):
                return "winget"
        return "msi"
    # Linux — 按常见发行版优先级检测
    for mgr in ("apt-get", "dnf", "yum", "apk", "pacman", "zypper"):
        if shutil.which(mgr):
            return mgr.replace("-get", "")  # apt-get → apt
    return ""


def _init_system(os_type: str) -> str:
    if os_type == "windows":
        return "windows"
    if os_type == "darwin":
        return "launchd"
    # Linux
    from core.service import systemd_is_available

    if systemd_is_available():
        return "systemd"
    if Path("/etc/init.d").exists() and shutil.which("service"):
        return "sysvinit"
    if shutil.which("rc-service"):
        return "openrc"
    return ""


def _is_root(os_type: str) -> bool:
    if os_type == "windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _disk_free(path: Path | str | None = None) -> int | None:
    """Read the filesystem of a target or its nearest existing ancestor."""
    try:
        target = Path.cwd() if path is None else Path(path).absolute()
        while True:
            try:
                target.stat()
                break
            except FileNotFoundError:
                # A dangling link is an existing path with an unresolved
                # destination, not a missing child on this filesystem.
                # Reporting its parent's free space would be misleading.
                try:
                    target.lstat()
                except FileNotFoundError:
                    pass
                else:
                    return None
                if target.parent == target:
                    return None
                target = target.parent
        return shutil.disk_usage(target).free
    except (OSError, ValueError):
        return None


def _cmd_ok(cmd: list[str]) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


# ─── 预检 ─────────────────────────────────────────────────────────────────────

def preflight_check() -> list[Issue]:
    """
    启动时一次性检测，返回问题列表。

    检测项：
    - OS 类型 + 版本
    - 架构（x86_64 / aarch64 / armv7）
    - 包管理器是否可用
    - 权限级别（root / admin / 普通用户）
    - 数据目录所在磁盘的剩余空间
    """
    from core.constants import MIN_DISK_FREE_BYTES
    from core.i18n import t
    from core.config import get_data_dir
    info = get_platform()
    issues: list[Issue] = []

    if not info.pkg_manager:
        issues.append(Issue(
            level=IssueLevel.WARN,
            message=t("platform.no_pkg_manager"),
            suggestion=t("platform.no_pkg_manager_suggestion"),
            code="no_pkg_manager",
        ))

    if not info.is_root:
        issues.append(Issue(
            level=IssueLevel.WARN,
            message=t("platform.non_root"),
            suggestion=t("platform.non_root_suggestion"),
            code="non_root",
        ))

    free_bytes = _disk_free(get_data_dir())
    if free_bytes is None:
        issues.append(Issue(
            level=IssueLevel.WARN,
            message=t("platform.disk_unknown"),
            suggestion=t("platform.disk_unknown_suggestion"),
            code="disk_unknown",
        ))
    elif free_bytes < MIN_DISK_FREE_BYTES:
        free_mb = free_bytes // 1024 // 1024
        issues.append(Issue(
            level=IssueLevel.WARN,
            message=t("platform.low_disk", free_mb=free_mb),
            suggestion=t("platform.low_disk_suggestion"),
            code="low_disk",
        ))

    return issues


def check_disk_space(required_bytes: int, path: Path | str | None = None) -> bool | None:
    """Check a target now; return None when its available space is unknown.

    Omitting the path retains the existing working-directory default.  Callers
    that know the actual target should supply it rather than assume that it is
    on the working-directory filesystem.
    """
    free = _disk_free(path)
    return None if free is None else free >= required_bytes
