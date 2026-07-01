"""跨平台共用工具：uv 路径、快照、版本查找、版本列表数据"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .constants import PYTHON_VERSIONS_FALLBACK, SNAPSHOT_SUBDIR, SNAPSHOT_PYTHON_FILE
from software._shared.snapshot import SnapshotStore

# ─── 各发行版包管理器可直接安装的 Python minor 版本 ────────────────────────────
_APT_VERSIONS: dict[str, list[str]] = {
    "debian:9":     ["3.5"],
    "debian:10":    ["3.7"],
    "debian:11":    ["3.9"],
    "debian:12":    ["3.11"],
    "debian:13":    ["3.12", "3.13"],
    "ubuntu:16.04": ["3.5"],
    "ubuntu:18.04": ["3.6", "3.7", "3.8"],
    "ubuntu:20.04": ["3.8", "3.9"],
    "ubuntu:22.04": ["3.10", "3.11"],
    "ubuntu:24.04": ["3.12"],
    "ubuntu:24.10": ["3.12", "3.13"],
    "ubuntu:25.04": ["3.13"],
    "ubuntu:25.10": ["3.13", "3.14"],
    "linuxmint:20": ["3.8"],
    "linuxmint:21": ["3.10"],
    "linuxmint:22": ["3.12"],
    "raspbian:10":  ["3.7"],
    "raspbian:11":  ["3.9"],
    "raspbian:12":  ["3.11"],
    "kali":         ["3.11"],
}

_DNF_VERSIONS: dict[str, list[str]] = {
    "centos:7":        ["3.6"],
    "centos:8":        ["3.8"],
    "rhel:7":          ["3.6"],
    "rhel:8":          ["3.8", "3.9"],
    "rhel:9":          ["3.9", "3.11"],
    "rhel:10":         ["3.12"],
    "rocky:8":         ["3.8", "3.9"],
    "rocky:9":         ["3.9", "3.11"],
    "almalinux:8":     ["3.8", "3.9"],
    "almalinux:9":     ["3.9", "3.11"],
    "fedora:38":       ["3.11"],
    "fedora:39":       ["3.12"],
    "fedora:40":       ["3.12"],
    "fedora:41":       ["3.13"],
    "fedora:42":       ["3.13"],
    "ol:8":            ["3.8", "3.9"],
    "ol:9":            ["3.9", "3.11"],
}

_ZYPPER_VERSIONS: dict[str, list[str]] = {
    "opensuse-leap:15.4": ["3.10"],
    "opensuse-leap:15.5": ["3.11"],
    "opensuse-leap:15.6": ["3.11"],
    "opensuse-tumbleweed": [],
    "suse:15":            ["3.6"],
}

_ROLLING_DISTROS = {
    "arch", "manjaro", "garuda", "endeavouros",
    "alpine",
    "opensuse-tumbleweed",
}
_ROLLING_PKG_MANAGERS = {"brew", "choco", "winget", "msi"}


@dataclass
class VersionEntry:
    """内部版本条目"""
    display: str
    need_build: bool
    full_ver: str = field(init=False)

    def __post_init__(self) -> None:
        self.full_ver = self.display


def get_pkg_minors(distro: str, ver_id: str, pkg_manager: str) -> list[str] | None:
    """
    返回包管理器可直接安装的 Python minor 列表。
    返回 None → rolling/brew/Windows，need_build 永远 False。
    返回空列表 → 发行版不在表中，全部需源码编译。
    """
    if distro in _ROLLING_DISTROS or pkg_manager in _ROLLING_PKG_MANAGERS:
        return None

    table: dict[str, list[str]]
    if pkg_manager == "apt":
        table = _APT_VERSIONS
    elif pkg_manager in ("dnf", "yum"):
        table = _DNF_VERSIONS
    elif pkg_manager == "zypper":
        table = _ZYPPER_VERSIONS
    else:
        return []

    key = f"{distro}:{ver_id}"
    if key in table:
        return table[key]
    major_key = f"{distro}:{ver_id.split('.')[0]}"
    if major_key in table:
        return table[major_key]
    return table.get(distro, [])


# ─── uv / shim 路径 ──────────────────────────────────────────────────────────

def uv_bin_path() -> Path:
    from .constants import UV_BIN_SUBDIR
    exe = "uv.exe" if os.name == "nt" else "uv"
    return Path.home() / UV_BIN_SUBDIR / exe


def uv_python_dir() -> Path:
    from .constants import UV_PYTHON_SUBDIR
    return Path.home() / UV_PYTHON_SUBDIR


def shim_dir() -> Path:
    from .constants import UV_SHIM_SUBDIR
    return Path.home() / UV_SHIM_SUBDIR


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

_store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_PYTHON_FILE)


def snapshot_path() -> Path:
    return _store.path


def load_snapshot() -> dict:
    return _store.load()


def save_snapshot(data: dict) -> None:
    _store.save(data)


def delete_snapshot() -> None:
    _store.delete()


# ─── Python 可执行路径查找 ────────────────────────────────────────────────────

def find_uv_python(version: str) -> str | None:
    """
    定位 uv 安装的指定版本 Python 可执行路径。
    Linux/macOS: ~/.opskit/python/cpython-{ver}-linux-.../bin/python3.X
    Windows:     ~/.opskit/python/cpython-{ver}-windows-.../python.exe
    """
    major_minor = ".".join(version.split(".")[:2])
    base = uv_python_dir()
    if not base.exists():
        return None
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith(f"cpython-{version}"):
            continue
        candidates = [
            entry / "bin" / f"python{major_minor}",
            entry / "bin" / "python3",
            entry / "python.exe",
            entry / f"python{major_minor}.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
    return None


# ─── 版本条目列表 ─────────────────────────────────────────────────────────────

_PY_STABLE_RE = __import__("re").compile(r"^\d+(?:\.\d+)*$")


def is_stable_pyver(version: str) -> bool:
    """仅纯数字点分（如 3.14.0）视为正式版；含 a/b/rc 等字母的预发布返回 False。"""
    return bool(_PY_STABLE_RE.match(version or ""))


def version_entries() -> list[VersionEntry]:
    """
    返回带编译标记的版本条目列表。
    数据来源优先级：1. uv list  2. endoflife.date  3. 硬编码 fallback

    原始版本列表经 ``resolve_versions`` 走会话级缓存：本次启动应用后 install /
    upgrade 首次进入才联网拉取一次，之后复用同一份缓存，避免每次进入都跑
    ``uv python list``（很慢）。编译标记（need_build）依赖本地平台信息，快速计算，
    不参与缓存。
    """
    import re as _re
    from core.constants import TIMEOUT_VERSION_FETCH
    from software._shared.version_resolver import resolve_versions
    from .constants import PYTHON_EOL_API
    from core.platform import get_platform

    def _fetch() -> list[str]:
        raw: list[str] = []

        uv = uv_bin_path()
        if uv.exists():
            try:
                result = subprocess.run(
                    [str(uv), "python", "list", "--all-versions"],
                    capture_output=True, text=True, timeout=15,
                )
                seen: set[str] = set()
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if not line.startswith("cpython-"):
                        continue
                    m = _re.match(r"cpython-(\d+\.\d+\.\d+)-", line)
                    if not m:
                        continue
                    ver = m.group(1)
                    if ver in seen:
                        continue
                    seen.add(ver)
                    raw.append(ver)
            except Exception:
                pass

        if not raw:
            try:
                import httpx
                resp = httpx.get(PYTHON_EOL_API, timeout=TIMEOUT_VERSION_FETCH)
                if resp.status_code == 200:
                    data = resp.json()
                    raw = [
                        d["latest"] for d in data
                        if not d.get("eol") or str(d["eol"]) > "2024"
                    ]
            except Exception:
                pass

        return raw

    raw = resolve_versions("python", _fetch, list(PYTHON_VERSIONS_FALLBACK))
    # 仅保留正式版：排除 a/b/rc 等预发布（如 3.14.0a7），保证安装/升级/切换一致
    raw = [v for v in raw if is_stable_pyver(v)]

    info = get_platform()
    pkg_minors = get_pkg_minors(info.os_name, info.os_version, info.pkg_manager)

    entries: list[VersionEntry] = []
    for ver in raw:
        minor = ".".join(ver.split(".")[:2])
        need_build = False if pkg_minors is None else minor not in pkg_minors
        entries.append(VersionEntry(display=ver, need_build=need_build))
    return entries


# ─── 源码编译 fallback（Linux/macOS 专用）────────────────────────────────────

def build_from_source(version: str) -> str | None:
    """从 python.org 下载源码包并编译安装（uv 不可用时的最终兜底）"""
    import tempfile
    from .constants import (
        PYTHON_SRC_URL,
        PYTHON_SRC_NPROC_MAX,
        PYTHON_BUILD_TIMEOUT,
    )

    src_url = PYTHON_SRC_URL.format(full_ver=version)
    nproc = min(os.cpu_count() or 1, PYTHON_SRC_NPROC_MAX)
    major_minor = ".".join(version.split(".")[:2])

    with tempfile.TemporaryDirectory(prefix="opskit-py-build-") as tmpdir:
        tarball = os.path.join(tmpdir, f"Python-{version}.tar.xz")
        src_dir = os.path.join(tmpdir, f"Python-{version}")

        dl = shutil.which("wget") or shutil.which("curl")
        if not dl:
            raise InstallError(t("software.python_error.download_src_failed", version=version, error="wget/curl not found"))
        cmd_dl = [dl, "-q", "-O", tarball, src_url] if "wget" in dl else [dl, "-fsSL", "-o", tarball, src_url]
        try:
            subprocess.run(cmd_dl, check=True, timeout=300)
        except Exception as e:
            raise InstallError(t("software.python_error.download_src_failed", version=version, error=e)) from e
        try:
            subprocess.run(["tar", "-Jxf", tarball, "-C", tmpdir], check=True, timeout=120)
        except Exception as e:
            raise InstallError(t("software.python_error.extract_src_failed", version=version, error=e)) from e
        try:
            subprocess.run(
                ["./configure", "--enable-optimizations", "--with-ensurepip=install"],
                check=True, cwd=src_dir, timeout=300,
            )
        except Exception as e:
            raise InstallError(t("software.python_error.configure_failed", version=version, error=e)) from e
        try:
            subprocess.run(["make", f"-j{nproc}"], check=True, cwd=src_dir, timeout=PYTHON_BUILD_TIMEOUT)
        except Exception as e:
            raise InstallError(t("software.python_error.compile_failed", version=version, error=e)) from e
        try:
            subprocess.run(["make", "altinstall"], check=True, cwd=src_dir, timeout=120)
        except Exception as e:
            raise InstallError(t("software.python_error.install_src_failed", version=version, error=e)) from e

    return shutil.which(f"python{major_minor}") or shutil.which("python3")
