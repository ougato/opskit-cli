"""Windows 平台驱动：zip 解压、shim cmd、注册表 PATH 注入"""
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
    WIN_GO_PS_MARKER,
    WIN_GO_PS_END,
    WIN_GO_CMD_MARKER,
    WIN_GO_CMD_END,
    CMD_AUTORUN_GO_SUBPATH,
    SHIM_GO_CMD_TEMPLATE,
    GOLANG_WIN_BIN_SUBDIR,
)


class WindowsDriver(PlatformDriver):

    # ─── tarball（zip）安装 ───────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 zip 解压到版本专属目录：~/.opskit/go/go{version}/
        返回 bin 目录路径字符串。
        """
        from .common import go_version_dir, go_bin_dir
        from core.archive import extract_zip
        from software._shared.archive_install import staged_directory
        dest = go_version_dir(version)
        try:
            with staged_directory(dest, prefix=".opskit-go-") as staging:
                with open_zip(str(tarball), "r") as zf:
                    if any(not member.filename.startswith("go/") for member in zf.infolist()):
                        raise ValueError("unexpected Go archive root")
                    extract_zip(zf, staging, strip_components=1)
                if not (staging / "bin/go.exe").is_file():
                    raise InstallError(t("software.golang_error.bad_structure", version=version, file="bin/go.exe"))
        except FileExistsError as e:
            raise InstallError("Go version directory already exists; refusing to overwrite") from e
        except InstallError:
            raise
        except Exception as e:
            raise InstallError(t("software.golang_error.extract_failed", version=version, error=e)) from e

        bin_dir = go_bin_dir(version)
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        from software._shared.windows_commands import route_text
        sdir = shim_dir()
        routes = {cmd + ".cmd": route_text(sdir, self._active_junction() / "bin" / (cmd + ".exe"), "golang")
                  for cmd in ("go", "gofmt")}
        with self._command_files(sdir, routes):
            pass

    def _command_files(self, directory, replacements):
        from software._shared.windows_commands import command_transaction, route_owned
        from .common import go_versions_dir
        return command_transaction(directory, replacements, owned=lambda name, text: route_owned(
            directory, name, text, "golang", go_versions_dir(), (SHIM_GO_CMD_TEMPLATE,)))

    def remove_shim(self) -> None:
        from .common import shim_dir
        with self._command_files(shim_dir(), {cmd + ".cmd": None for cmd in ("go", "gofmt")}):
            pass

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(";"))

    # ─── version link ────────────────────────────────────────────────────────
    # 方案：directory junction ~\.opskit\go\active 始终指向激活版本目录。
    # PATH 注入 active\bin（一次性写入注册表），切换时只改 junction 目标。
    # 已打开的 PowerShell 无需重启，go version 立即无感知生效。

    def _win_bin_dir(self) -> Path:
        return Path.home() / GOLANG_WIN_BIN_SUBDIR

    def _active_junction(self) -> Path:
        from .common import go_versions_dir
        return go_versions_dir() / "active"

    def _set_junction(self, target_dir: str) -> bool:
        from software._shared.windows_junction import set_junction
        from .common import go_versions_dir
        try:
            set_junction(self._active_junction(), target_dir, go_versions_dir())
            return True
        except OSError:
            return False

    def apply_version_link(self, bin_dir: str) -> None:
        """
        切换时更新 junction ~/.opskit/go/active -> 激活版本目录。
        PATH 里固定写 active/bin，junction 切换后已开的 PowerShell 立即感知，
        无需任何用户操作，完全无感知。
        """
        from software._shared.windows_version_routing import activate_route
        from .common import go_versions_dir
        activate_route(self, bin_dir, managed_root=go_versions_dir(), feature="golang",
                       commands={"go": (".exe",), "gofmt": (".exe",)}, bin_subdir=True)

    def restore_original(self) -> None:
        from software._shared.windows_version_routing import restore_route
        from .common import go_versions_dir
        restore_route(self, managed_root=go_versions_dir(), commands=("go", "gofmt"), bin_subdir=True)

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, go_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            go_exe = go_bin_dir(active) / "go.exe"
            if go_exe.exists():
                return active
        go_cmd = shutil.which("go")
        if go_cmd:
            try:
                r = subprocess.run([go_cmd, "version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if line.startswith("go version go"):
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2][2:]
            except Exception:
                pass
        return None

    # ─── 用户 PATH 路由────────────────────────────────────────────────────

    def _inject_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_GO_PS_MARKER, WIN_GO_PS_END, remove=False, verify_commands=verify_commands)

        # 4. 当前进程 PATH
        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(";"):
            os.environ["PATH"] = shims_path + ";" + cur_path

    def _remove_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_GO_PS_MARKER, WIN_GO_PS_END, remove=True, verify_commands=verify_commands)

    def _remove_paths(self, directories, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import remove_user_paths
        remove_user_paths(directories, WIN_GO_PS_MARKER, WIN_GO_PS_END, verify_commands=verify_commands)

    def _retire_legacy_autorun(self) -> None:
        from software._shared.windows_autorun import retire_legacy_autorun
        from .common import shim_dir
        retire_legacy_autorun(
            Path.home() / CMD_AUTORUN_GO_SUBPATH,
            (str(shim_dir()), str(self._win_bin_dir()), str(self._active_junction() / "bin")),
            WIN_GO_CMD_MARKER, WIN_GO_CMD_END,
        )
