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
    NODEJS_WIN_BIN_SUBDIR,
    WIN_NODE_PS_MARKER,
    WIN_NODE_PS_END,
    WIN_NODE_CMD_MARKER,
    WIN_NODE_CMD_END,
    CMD_AUTORUN_NODE_SUBPATH,
    SHIM_NODE_CMD_TEMPLATE,
)

_SHIM_CMDS = ("node", "npm", "npx")


class WindowsDriver(PlatformDriver):

    # ─── zip 安装 ─────────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 zip 解压到版本专属目录：~/.opskit/nodejs/nodeX.Y.Z/
        Node 官方 Windows 包内层格式：node-vX.Y.Z-win-x64/
        返回 bin 目录路径字符串（Windows 下 node.exe 直接在根目录）。
        """
        from .common import node_version_dir
        from core.archive import extract_zip
        from software._shared.archive_install import staged_directory
        dest = node_version_dir(version)
        try:
            with staged_directory(dest, prefix=".opskit-node-") as staging:
                with open_zip(str(tarball), "r") as zf:
                    extract_zip(zf, staging, strip_components=int(any("/" in member.filename for member in zf.infolist())))
                if not (staging / "node.exe").is_file():
                    raise InstallError(t("software.nodejs_error.bad_structure", version=version, file="node.exe"))
        except FileExistsError as e:
            raise InstallError("Node.js version directory already exists; refusing to overwrite") from e
        except InstallError:
            raise
        except Exception as e:
            raise InstallError(t("software.nodejs_error.extract_failed", version=version, error=e)) from e

        return str(dest)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from software._shared.windows_commands import route_text
        wbin = self._win_bin_dir()
        routes = {cmd + ".cmd": route_text(wbin, self._active_junction() / (cmd + (".exe" if cmd == "node" else ".cmd")), "nodejs")
                  for cmd in _SHIM_CMDS}
        with self._command_files(wbin, routes):
            pass

    def _command_files(self, directory, replacements):
        from software._shared.windows_commands import command_transaction, route_owned
        from .common import node_versions_dir
        return command_transaction(directory, replacements, owned=lambda name, text: route_owned(
            directory, name, text, "nodejs", node_versions_dir(), (SHIM_NODE_CMD_TEMPLATE.format(cmd=Path(name).stem),)))

    def remove_shim(self) -> None:
        directory = self._win_bin_dir()
        with self._command_files(directory, {cmd + ".cmd": None for cmd in _SHIM_CMDS}) as verify:
            self._remove_path(str(directory), verify_commands=verify)

    def shim_active(self) -> bool:
        wbin = str(self._win_bin_dir())
        return any(p == wbin for p in os.environ.get("PATH", "").split(";"))

    # ─── version link（directory junction） ───────────────────────────────────
    # 方案：junction ~/.opskit/nodejs/active 始终指向激活版本目录。
    # PATH 注入 active/（一次性写入注册表），切换时只改 junction 目标。
    # 已打开的 PowerShell 无需重启，node --version 立即无感知生效。

    def _win_bin_dir(self) -> Path:
        return Path.home() / NODEJS_WIN_BIN_SUBDIR

    def _active_junction(self) -> Path:
        from .common import node_versions_dir
        return node_versions_dir() / "active"

    def _set_junction(self, target_dir: str) -> bool:
        from software._shared.windows_junction import set_junction
        from .common import node_versions_dir
        try:
            set_junction(self._active_junction(), target_dir, node_versions_dir())
            return True
        except OSError:
            return False

    def apply_version_link(self, bin_dir: str) -> None:
        """
        切换时更新 junction ~/.opskit/nodejs/active -> 激活版本目录。
        PATH 里固定写 active/（junction 切换后已开的 PowerShell 立即感知），
        无需任何用户操作，完全无感知。
        注意：Windows Node 官方包 node.exe 在根目录，无 bin/ 子目录。
        """
        from software._shared.windows_version_routing import activate_route
        from .common import node_versions_dir
        activate_route(self, bin_dir, managed_root=node_versions_dir(), feature="nodejs",
                       commands={"node": (".exe",), "npm": (".cmd",), "npx": (".cmd",)})

    def restore_original(self) -> None:
        from software._shared.windows_version_routing import restore_route
        from .common import node_versions_dir
        restore_route(self, managed_root=node_versions_dir(), commands=_SHIM_CMDS)

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, node_version_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            node_exe = node_version_dir(active) / "node.exe"
            if node_exe.exists():
                return active
        node_cmd = shutil.which("node")
        if node_cmd:
            try:
                r = subprocess.run([node_cmd, "--version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if line.startswith("v"):
                    return line[1:]
            except Exception:
                pass
        return None

    # ─── 用户 PATH 路由────────────────────────────────────────────────────

    def _inject_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_NODE_PS_MARKER, WIN_NODE_PS_END, remove=False, verify_commands=verify_commands)

        # 4. 当前进程 PATH
        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(";"):
            os.environ["PATH"] = shims_path + ";" + cur_path

    def _remove_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_NODE_PS_MARKER, WIN_NODE_PS_END, remove=True, verify_commands=verify_commands)

    def _remove_paths(self, directories, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import remove_user_paths
        remove_user_paths(directories, WIN_NODE_PS_MARKER, WIN_NODE_PS_END, verify_commands=verify_commands)

    def _retire_legacy_autorun(self) -> None:
        from software._shared.windows_autorun import retire_legacy_autorun
        from .common import shim_dir
        retire_legacy_autorun(
            Path.home() / CMD_AUTORUN_NODE_SUBPATH,
            (str(shim_dir()), str(self._win_bin_dir()), str(self._active_junction())),
            WIN_NODE_CMD_MARKER, WIN_NODE_CMD_END,
        )
