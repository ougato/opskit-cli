"""跨平台包管理器策略模式

用法：
    from core.pkg_runner import get_runner
    get_runner().install(["wireguard-tools"])
    get_runner().install_extras(["epel-release"])   # 仅部分平台有意义

添加新发行版：只需新增一个 Runner 子类并在 _REGISTRY 注册，业务代码零改动。
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any


class UnsupportedPackageManager(RuntimeError):
    pass


# ─── 基类 ─────────────────────────────────────────────────────────────────────

class PkgRunner:
    """包管理器基类，子类按需覆盖方法"""

    name: str = ""
    # 是否需要 root 权限执行（Linux 系统级包管理器为 True）。
    # 为 True 时，命令统一经 core.privilege.run_as_root 执行：
    # 非 root 自动加 sudo，已是 root 直接执行。这样普通用户也能
    # 正常安装/卸载/升级，避免装时提权、卸时不提权的不对称问题。
    needs_root: bool = False

    def update_index(self) -> None:
        """更新软件源索引（apt update / yum makecache 等）"""

    def install(self, packages: list[str], quiet: bool = True) -> None:
        """安装一组包，失败抛出 subprocess.CalledProcessError"""
        raise NotImplementedError

    def remove(self, packages: list[str]) -> None:
        """卸载一组包"""
        raise NotImplementedError

    def install_extras(self, packages: list[str]) -> None:
        """
        安装前置/扩展包（如 epel-release、elrepo-release）。
        不抛出异常，仅尽力而为。
        """

    # ── 内部工具 ──────────────────────────────────────────────────────────────
    def _run(self, cmd: list[str], check: bool = True, **kw: Any) -> subprocess.CompletedProcess:
        if self.needs_root:
            from core.privilege import run_as_root
            return run_as_root(cmd, check=check, capture_output=True, text=True, **kw)
        return subprocess.run(cmd, check=check, capture_output=True, text=True, **kw)


# ─── Apt（Debian / Ubuntu）────────────────────────────────────────────────────

class AptRunner(PkgRunner):
    name = "apt"
    needs_root = True

    def update_index(self) -> None:
        self._run(["apt-get", "update", "-qq"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        flags = ["-y", "-qq"] if quiet else ["-y"]
        self._run(["apt-get", "install"] + flags + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["apt-get", "remove", "-y"] + packages, check=False)

    def install_venv_pkg(self, python_bin: str) -> None:
        """安装 python3.X-venv（Debian 特有，ensurepip 缺失时调用）"""
        try:
            r = self._run([python_bin, "--version"], check=False)
            ver_str = (r.stdout.strip() or r.stderr.strip()).split()[-1]
            major_minor = ".".join(ver_str.split(".")[:2])
            pkg = f"python{major_minor}-venv"
            self._run(["apt-get", "install", "-y", pkg], check=False)
        except Exception:
            pass

    def install_python3(self, ver: str = "3.11") -> str | None:
        """安装 python3.X，返回可执行路径"""
        try:
            self._run(["apt-get", "install", "-y", f"python{ver}", f"python{ver}-venv"])
            return shutil.which(f"python{ver}") or shutil.which("python3")
        except Exception:
            return None


# ─── Yum（CentOS 7 / RHEL 7）────────────────────────────────────────────────

class YumRunner(PkgRunner):
    name = "yum"
    needs_root = True

    def update_index(self) -> None:
        self._run(["yum", "makecache", "-q"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        self._run(["yum", "install", "-y"] + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["yum", "remove", "-y"] + packages, check=False)

    def install_extras(self, packages: list[str]) -> None:
        for pkg in packages:
            self._run(["yum", "install", "-y", pkg], check=False)

    def install_python3(self, ver: str = "3.11") -> str | None:
        try:
            self._run(["yum", "install", "-y", "python3"])
            return shutil.which("python3")
        except Exception:
            return None


# ─── Dnf（CentOS 8 / Rocky / AlmaLinux / Fedora）────────────────────────────

class DnfRunner(PkgRunner):
    name = "dnf"
    needs_root = True

    def update_index(self) -> None:
        self._run(["dnf", "makecache", "-q"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        self._run(["dnf", "install", "-y"] + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["dnf", "remove", "-y"] + packages, check=False)

    def install_extras(self, packages: list[str]) -> None:
        for pkg in packages:
            self._run(["dnf", "install", "-y", pkg], check=False)

    def install_python3(self, ver: str = "3.11") -> str | None:
        try:
            self._run(["dnf", "install", "-y", f"python{ver}"])
            return shutil.which(f"python{ver}") or shutil.which("python3")
        except Exception:
            return None


# ─── Apk（Alpine）────────────────────────────────────────────────────────────

class ApkRunner(PkgRunner):
    name = "apk"
    needs_root = True

    def update_index(self) -> None:
        self._run(["apk", "update"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        flags = ["--no-cache"] if quiet else []
        self._run(["apk", "add"] + flags + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["apk", "del"] + packages, check=False)

    def install_python3(self, ver: str = "3.11") -> str | None:
        try:
            self._run(["apk", "add", "--no-cache", "python3", "py3-pip"])
            return shutil.which("python3")
        except Exception:
            return None


# ─── Pacman（Arch / Manjaro）─────────────────────────────────────────────────

class PacmanRunner(PkgRunner):
    name = "pacman"
    needs_root = True

    def update_index(self) -> None:
        self._run(["pacman", "-Sy", "--noconfirm"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        self._run(["pacman", "-S", "--noconfirm"] + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["pacman", "-R", "--noconfirm"] + packages, check=False)

    def install_python3(self, ver: str = "3.11") -> str | None:
        try:
            self._run(["pacman", "-S", "--noconfirm", "python"])
            return shutil.which("python3") or shutil.which("python")
        except Exception:
            return None


# ─── Zypper（openSUSE）───────────────────────────────────────────────────────

class ZypperRunner(PkgRunner):
    name = "zypper"
    needs_root = True

    def update_index(self) -> None:
        self._run(["zypper", "refresh"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        self._run(["zypper", "install", "-y"] + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["zypper", "remove", "-y"] + packages, check=False)

    def install_python3(self, ver: str = "3.11") -> str | None:
        try:
            self._run(["zypper", "install", "-y", "python3"])
            return shutil.which("python3")
        except Exception:
            return None


# ─── Brew（macOS / Linux Homebrew）──────────────────────────────────────────

class BrewRunner(PkgRunner):
    name = "brew"

    def update_index(self) -> None:
        self._run(["brew", "update"], check=False)

    def install(self, packages: list[str], quiet: bool = True) -> None:
        self._run(["brew", "install"] + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["brew", "uninstall"] + packages, check=False)

    def install_python3(self, ver: str = "3.11") -> str | None:
        try:
            self._run(["brew", "install", f"python@{ver}"])
            return shutil.which(f"python{ver}") or shutil.which("python3")
        except Exception:
            return None


# ─── Choco（Windows）─────────────────────────────────────────────────────────

class ChocoRunner(PkgRunner):
    name = "choco"

    def install(self, packages: list[str], quiet: bool = True) -> None:
        self._run(["choco", "install", "-y"] + packages)

    def remove(self, packages: list[str]) -> None:
        self._run(["choco", "uninstall", "-y"] + packages, check=False)


# ─── Winget（Windows 10 1709+）───────────────────────────────────────────────

class WingetRunner(PkgRunner):
    name = "winget"

    def install(self, packages: list[str], quiet: bool = True) -> None:
        for pkg in packages:
            self._run(["winget", "install", "--silent", "--accept-source-agreements",
                       "--accept-package-agreements", pkg])

    def remove(self, packages: list[str]) -> None:
        for pkg in packages:
            self._run(["winget", "uninstall", "--silent", pkg], check=False)


# ─── MSI（Windows 无包管理器兜底）────────────────────────────────────────────

class MsiRunner(PkgRunner):
    name = "msi"

    def install(self, packages: list[str], quiet: bool = True) -> None:
        raise UnsupportedPackageManager(
            "Windows 未检测到 choco/winget，请手动安装：" + ", ".join(packages)
        )


# ─── Registry ────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[PkgRunner]] = {
    "apt":     AptRunner,
    "yum":     YumRunner,
    "dnf":     DnfRunner,
    "apk":     ApkRunner,
    "pacman":  PacmanRunner,
    "zypper":  ZypperRunner,
    "brew":    BrewRunner,
    "choco":   ChocoRunner,
    "winget":  WingetRunner,
    "msi":     MsiRunner,
}

_cached_runner: PkgRunner | None = None


def get_runner() -> PkgRunner:
    """
    返回当前平台对应的包管理器 Runner 实例（进程内缓存）。

    pkg_manager 由 core.platform.get_platform() 检测，
    检测顺序：apt > dnf > yum > apk > pacman > zypper > brew > choco > winget > msi
    """
    global _cached_runner
    if _cached_runner is not None:
        return _cached_runner

    from core.platform import get_platform
    mgr = get_platform().pkg_manager
    cls = _REGISTRY.get(mgr)
    if cls is None:
        raise UnsupportedPackageManager(
            f"未知包管理器 '{mgr}'，请向 core/pkg_runner.py 的 _REGISTRY 中添加对应实现"
        )
    _cached_runner = cls()
    return _cached_runner


def reset_runner() -> None:
    """测试用：清除缓存，强制下次重新检测"""
    global _cached_runner
    _cached_runner = None
