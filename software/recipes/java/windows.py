"""Windows 平台驱动：zip 解压、shim cmd、注册表 PATH 注入、directory junction"""
from __future__ import annotations

from core.archive_reader import open_zip

import os
import shutil
import subprocess
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import (
    JAVA_WIN_BIN_SUBDIR,
    WIN_JAVA_PS_MARKER,
    WIN_JAVA_PS_END,
    WIN_JAVA_CMD_MARKER,
    WIN_JAVA_CMD_END,
    CMD_AUTORUN_JAVA_SUBPATH,
    JAVA_SHIM_CMDS,
    SHIM_JAVA_CMD_TEMPLATE,
)


class WindowsDriver(PlatformDriver):

    # ─── zip 安装 ─────────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 zip 解压到版本专属目录：~/.opskit/java/jdk{version}/
        Temurin Windows 包内层格式：jdk-21.0.11+10/ 或 jdk-17.0.19+10/
        返回 bin 目录路径字符串。
        """
        from .common import java_version_dir, java_bin_dir
        from core.archive import extract_zip
        from software._shared.archive_install import staged_directory
        dest = java_version_dir(version)
        try:
            with staged_directory(dest, prefix=".opskit-java-") as staging:
                with open_zip(str(tarball), "r") as zf:
                    extract_zip(zf, staging, strip_components=int(any("/" in member.filename for member in zf.infolist())))
                if not (staging / "bin/java.exe").is_file():
                    raise InstallError(t("software.java_error.bad_structure", version=version, file="bin/java.exe"))
        except FileExistsError as e:
            raise InstallError("Java version directory already exists; refusing to overwrite") from e
        except InstallError:
            raise
        except Exception as e:
            raise InstallError(t("software.java_error.extract_failed", version=version, error=e)) from e

        bin_d = java_bin_dir(version)
        return str(bin_d)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from software._shared.windows_commands import route_text
        wbin = self._win_bin_dir()
        routes = {cmd + ".cmd": route_text(wbin, self._active_junction() / "bin" / (cmd + ".exe"), "java")
                  for cmd in JAVA_SHIM_CMDS}
        with self._command_files(wbin, routes):
            pass

    def _command_files(self, directory, replacements):
        from software._shared.windows_commands import command_transaction, route_owned
        from .common import java_versions_dir
        return command_transaction(directory, replacements, owned=lambda name, text: route_owned(
            directory, name, text, "java", java_versions_dir(), (SHIM_JAVA_CMD_TEMPLATE.format(cmd=Path(name).stem),)))

    def remove_shim(self) -> None:
        with self._command_files(self._win_bin_dir(), {cmd + ".cmd": None for cmd in JAVA_SHIM_CMDS}):
            pass

    def shim_active(self) -> bool:
        wbin = str(self._win_bin_dir())
        return any(p == wbin for p in os.environ.get("PATH", "").split(";"))

    # ─── version link（directory junction） ───────────────────────────────────
    # 方案：junction ~/.opskit/java/active 始终指向激活版本目录。
    # PATH 注入 active/bin（一次性写入注册表），切换时只改 junction 目标。
    # 已打开的 PowerShell 无需重启，java -version 立即无感知生效。

    def _win_bin_dir(self) -> Path:
        return Path.home() / JAVA_WIN_BIN_SUBDIR

    def _active_junction(self) -> Path:
        from .common import java_versions_dir
        return java_versions_dir() / "active"

    def _set_junction(self, target_dir: str) -> bool:
        from software._shared.windows_junction import set_junction
        from .common import java_versions_dir
        try:
            set_junction(self._active_junction(), target_dir, java_versions_dir())
            return True
        except OSError:
            return False

    def apply_version_link(self, bin_dir: str) -> None:
        """
        切换时更新 junction ~/.opskit/java/active -> 激活版本目录。
        PATH 里固定写 active/bin，junction 切换后已开的 PowerShell 立即感知。
        """
        from software._shared.windows_version_routing import activate_route
        from .common import java_versions_dir
        activate_route(self, bin_dir, managed_root=java_versions_dir(), feature="java",
                       commands={cmd: (".exe",) for cmd in JAVA_SHIM_CMDS}, bin_subdir=True)

    def restore_original(self) -> None:
        from software._shared.windows_version_routing import restore_route
        from .common import java_versions_dir
        restore_route(self, managed_root=java_versions_dir(), commands=JAVA_SHIM_CMDS, bin_subdir=True)

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, java_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            java_exe = java_bin_dir(active) / "java.exe"
            if java_exe.exists():
                return active
        java_cmd = shutil.which("java")
        if java_cmd:
            try:
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

    # ─── 用户 PATH 路由────────────────────────────────────────────────────

    def _inject_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_JAVA_PS_MARKER, WIN_JAVA_PS_END, remove=False, verify_commands=verify_commands)

        # 4. 当前进程 PATH
        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(";"):
            os.environ["PATH"] = shims_path + ";" + cur_path

    def _remove_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_JAVA_PS_MARKER, WIN_JAVA_PS_END, remove=True, verify_commands=verify_commands)

    def _remove_paths(self, directories, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import remove_user_paths
        remove_user_paths(directories, WIN_JAVA_PS_MARKER, WIN_JAVA_PS_END, verify_commands=verify_commands)

    def _retire_legacy_autorun(self) -> None:
        from software._shared.windows_autorun import retire_legacy_autorun
        from .common import shim_dir
        retire_legacy_autorun(
            Path.home() / CMD_AUTORUN_JAVA_SUBPATH,
            (str(shim_dir()), str(self._win_bin_dir()), str(self._active_junction() / "bin")),
            WIN_JAVA_CMD_MARKER, WIN_JAVA_CMD_END,
        )
