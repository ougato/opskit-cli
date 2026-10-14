"""Linux 平台驱动：shim sh 脚本、symlink、shell rc PATH 注入、uv 安装"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from software.base import InstallError
from software._shared.activation import ActivationRollbackError
from .driver import PlatformDriver
from .constants import (
    SHIM_MARKER_BEGIN,
    SHIM_MARKER_END,
    PROFILE_D_SHIM_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── uv ──────────────────────────────────────────────────────────────────

    def ensure_uv(self) -> str:
        from .uv_bootstrap import ensure_uv_archive
        return ensure_uv_archive()

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        from .posix_shims import shim_files, shim_text
        from software._shared import shell_path
        directory = shim_dir()
        route, _ = self._symlink_target()
        with shim_files(directory, shim_text(fallback_bin, route), route) as verify:
            shell_path.inject_rc_path(str(directory), SHIM_MARKER_BEGIN, SHIM_MARKER_END, PROFILE_D_SHIM_FILE)
            verify()

    def remove_shim(self) -> None:
        from .common import shim_dir
        from .posix_shims import shim_files
        from software._shared import shell_path
        directory = shim_dir()
        route, _ = self._symlink_target()
        with shim_files(directory, None, route) as verify:
            shell_path.remove_rc_path(SHIM_MARKER_BEGIN, SHIM_MARKER_END, PROFILE_D_SHIM_FILE)
            verify()
        try:
            directory.rmdir()
        except OSError:
            pass  # Leave unrelated user files and nonempty directories intact.

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(":"))

    # ─── symlink / PATH ───────────────────────────────────────────────────────

    def apply_version_link(self, new_bin: str) -> None:
        symlink_path, has_root = self._symlink_target()
        from .constants import SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END
        target = Path(new_bin)
        if not target.is_absolute() or not target.is_file():
            raise InstallError("Python route requires an existing absolute interpreter path")
        self._change_link(symlink_path, target, lambda: None if has_root else
                          self._ensure_local_bin_in_path(SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END))

    def restore_original(
        self, symlink_path: str, original_target: str | None, had_local_bin_path: bool,
    ) -> None:
        from .constants import SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END
        expected, _ = self._symlink_target()
        if not symlink_path or Path(symlink_path).absolute() != Path(expected).absolute():
            raise InstallError("Python restore route is outside the platform command path")
        target = Path(original_target) if original_target else None
        self._change_link(symlink_path, target, lambda: self._remove_path_block(
            SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END) if had_local_bin_path else None)

    def _change_link(self, symlink_path, replacement, dependent):
        from software._shared import shell_path
        link = Path(symlink_path)
        for node in link.parents:
            try:
                info = node.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
                raise InstallError("Python command parent must not be a link")
        previous = shell_path._command_link(link)
        link.parent.mkdir(parents=True, exist_ok=True)
        if previous != replacement:
            shell_path._apply_command_links([(link, previous, replacement)])
        published = replacement
        try:
            current = shell_path._command_link(link)
            if ((current is None) != (replacement is None) or
                    (current is not None and shell_path._command_target(link, current) !=
                     shell_path._command_target(link, replacement))):
                raise InstallError("Python command link changed after publication")
            # Windows readlink uses extended path prefixes; preserve its actual
            # representation for the shared helper's exact rollback preflight.
            published = current
            dependent()
            if shell_path._command_link(link) != published:
                raise InstallError("Python command link changed before commit")
        except BaseException:
            if previous != replacement:
                try:
                    shell_path._apply_command_links([(link, published, previous)])
                except Exception:
                    raise ActivationRollbackError("Python command route failed; rollback incomplete") from None
            raise

    def snapshot_pre_install(self) -> dict:
        symlink_path, has_root = self._symlink_target()
        original_target = self._get_original_symlink_target(symlink_path)
        return {
            "symlink_path": symlink_path,
            "original_target": original_target,
            "had_local_bin_path": not has_root,
        }

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .version_probe import command_version
        return command_version(("python3", "python"))

    # ─── 内部工具 ─────────────────────────────────────────────────────────────

    def _symlink_target(self) -> tuple[str, bool]:
        has_root = (os.getuid() == 0) if hasattr(os, "getuid") else False
        if has_root:
            return "/usr/local/bin/python3", True
        return str(Path.home() / ".local" / "bin" / "python3"), False

    def _get_original_symlink_target(self, symlink_path: str) -> str | None:
        from software._shared import shell_path
        target = shell_path._command_link(Path(symlink_path))
        return str(target) if target is not None else None

    def _ensure_local_bin_in_path(self, begin_marker: str, end_marker: str) -> None:
        from software._shared import shell_path
        shell_path.inject_rc_path(str(Path.home() / ".local" / "bin"), begin_marker, end_marker)

    def _remove_path_block(self, begin_marker: str, end_marker: str) -> None:
        from software._shared import shell_path
        shell_path.remove_rc_path(begin_marker, end_marker)
