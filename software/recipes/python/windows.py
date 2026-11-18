"""Windows 平台驱动：.cmd shim、PATH、PS Profile 和受管旧 AutoRun 迁移。"""
from __future__ import annotations


import os
import re
import stat
from pathlib import Path

from .driver import PlatformDriver
from .constants import (
    CMD_AUTORUN_SUBPATH,
    WIN_SHIM_PS_MARKER,
    WIN_SHIM_PS_END,
    WIN_SHIM_CMD_MARKER,
    WIN_SHIM_CMD_END,
    SHIM_CMD_TEMPLATE,
)


class WindowsDriver(PlatformDriver):

    def __init__(self):
        from .common import load_snapshot
        # Activation publishes its new snapshot before applying the route.
        # Bind the previous target before that publication, not from the link.
        self._route_binary = load_snapshot().get("uv_python_path")

    # ─── uv ──────────────────────────────────────────────────────────────────

    def ensure_uv(self) -> str:
        from .uv_bootstrap import ensure_uv_archive
        return ensure_uv_archive()

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir, load_snapshot
        sdir = shim_dir()
        binary = getattr(self, "_route_binary", None) or load_snapshot().get("uv_python_path")
        relative = self._route_plan(binary)[1] if binary else "python.exe"
        body = self._route_text(relative)
        with self._command_files(sdir, {name: body for name in ("python.cmd", "python3.cmd")}) as verify:
            self._inject_path(str(sdir), verify_commands=verify)

    @staticmethod
    def _route_plan(binary, *, require_file=True):
        from software.base import InstallError
        path = Path(binary).absolute()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.exe", path.name):
            raise InstallError("unsupported Python executable filename")
        if require_file:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", 0):
                raise InstallError("Python route requires a regular executable")
        path = path.resolve(strict=require_file)
        if path.parent.name.lower() == "scripts" and (path.parent.parent / "pyvenv.cfg").is_file():
            return path.parent.parent, "Scripts\\" + path.name
        return path.parent, path.name

    @staticmethod
    def _route_text(relative):
        return ("@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
                "rem opskit python command v1\r\n"
                f'"%~dp0.python-current\\{relative}" %*\r\nexit /b %errorlevel%\r\n')

    @classmethod
    def _owned_command(cls, name, text):
        canonical = text.replace("\r\r\n", "\r\n").replace("\r\n", "\n").replace("\n", "\r\n")
        if canonical == SHIM_CMD_TEMPLATE:
            return True
        lines = canonical.splitlines()
        if len(lines) != 5:
            return False
        match = re.fullmatch(r'"%~dp0\.python-current\\((?:Scripts\\)?[A-Za-z0-9_.-]+\.exe)" %\*', lines[3])
        return match is not None and canonical == cls._route_text(match[1])

    def _authorized_roots(self, new_binary=None):
        from .common import load_snapshot
        binaries = (new_binary, getattr(self, "_route_binary", None), load_snapshot().get("uv_python_path"))
        return tuple(dict.fromkeys(self._route_plan(binary, require_file=False)[0] for binary in binaries if binary))

    def _command_files(self, directory, replacements):
        from software._shared.windows_commands import command_transaction
        return command_transaction(directory, replacements,
                                   owned=self._owned_command)

    def remove_shim(self) -> None:
        from .common import shim_dir
        from software._shared.windows_junction import junction_transaction, remove_junction
        sdir = shim_dir()
        entry = sdir / ".python-current"
        targets = self._authorized_roots() or None
        with junction_transaction(entry, sdir, authorized_targets=targets) as change:
            with self._command_files(sdir, {name: None for name in ("python.cmd", "python3.cmd")}) as verify:
                remove_junction(entry, sdir, authorized_targets=targets)
                change.checkpoint(removed=True)
                self._remove_path(str(sdir), verify_commands=verify)
                change.verify()
        self._route_binary = None

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(";"))

    # ─── 固定命令与受管 junction 路由 ─────────────────────────────────────────

    def apply_version_link(self, new_bin: str) -> None:
        from .common import shim_dir
        from software._shared.windows_junction import junction_transaction, set_junction
        sdir = shim_dir()
        target, relative = self._route_plan(new_bin)
        targets = self._authorized_roots(new_bin)
        entry = sdir / ".python-current"
        body = self._route_text(relative)
        with junction_transaction(entry, sdir, authorized_targets=targets) as change:
            with self._command_files(sdir, {name: body for name in ("python.cmd", "python3.cmd")}) as verify:
                set_junction(entry, target, sdir, authorized_targets=targets)
                change.checkpoint(target)
                verify()
                change.verify()
        self._route_binary = new_bin

    def restore_original(
        self,
        symlink_path: str,
        original_target: str | None,
        had_local_bin_path: bool,
    ) -> None:
        self.remove_shim()

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .version_probe import command_version
        return command_version(("python", "python3"))

    # ─── 用户 PATH 路由────────────────────────────────────────────────────

    def _inject_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_SHIM_PS_MARKER, WIN_SHIM_PS_END, remove=False, verify_commands=verify_commands)

        # 4. 当前进程 PATH
        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(";"):
            os.environ["PATH"] = shims_path + ";" + cur_path

    def _remove_path(self, shims_path: str, *, verify_commands=None) -> None:
        self._retire_legacy_autorun()
        from software._shared.windows_registry_path import change_user_path
        change_user_path(shims_path, WIN_SHIM_PS_MARKER, WIN_SHIM_PS_END, remove=True, verify_commands=verify_commands)
        os.environ["PATH"] = ";".join(part for part in os.environ.get("PATH", "").split(";")
                                       if os.path.normcase(part) != os.path.normcase(shims_path))

    def _retire_legacy_autorun(self) -> None:
        from software._shared.windows_autorun import retire_legacy_autorun
        from .common import shim_dir
        retire_legacy_autorun(
            Path.home() / CMD_AUTORUN_SUBPATH,
            (str(shim_dir()),),
            WIN_SHIM_CMD_MARKER, WIN_SHIM_CMD_END,
        )
