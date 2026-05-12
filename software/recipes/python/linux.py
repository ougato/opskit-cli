"""Linux 平台驱动：shim sh 脚本、symlink、shell rc PATH 注入、uv 安装"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import (
    SHIM_MARKER_BEGIN,
    SHIM_MARKER_END,
    SHIM_SH_TEMPLATE,
    PROFILE_D_SHIM_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── uv ──────────────────────────────────────────────────────────────────

    def ensure_uv(self) -> str:
        from .constants import UV_INSTALL_SH, UV_INSTALL_SH_GHPROXY, UV_INSTALL_TIMEOUT
        from .common import uv_bin_path

        sys_uv = shutil.which("uv")
        if sys_uv:
            return sys_uv

        private_uv = uv_bin_path()
        if private_uv.exists() and os.access(str(private_uv), os.X_OK):
            return str(private_uv)

        private_uv.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["UV_INSTALL_DIR"] = str(private_uv.parent)

        for url in (UV_INSTALL_SH_GHPROXY, UV_INSTALL_SH):
            dl = shutil.which("curl") or shutil.which("wget")
            if not dl:
                raise InstallError(t("software.python_error.download_src_failed", version="uv", error="curl/wget not found"))
            cmd = [dl, "-LsSf", url] if "curl" in dl else [dl, "-qO-", url]
            try:
                with tempfile.NamedTemporaryFile(suffix=".sh", delete=False, mode="w") as f:
                    result = subprocess.run(cmd, capture_output=True, timeout=UV_INSTALL_TIMEOUT)
                    if result.returncode != 0:
                        continue
                    f.write(result.stdout.decode())
                    script = f.name
                subprocess.run(
                    ["sh", script],
                    env=env,
                    check=True,
                    timeout=UV_INSTALL_TIMEOUT,
                    capture_output=True,
                    text=True,
                )
                os.unlink(script)
                if private_uv.exists():
                    return str(private_uv)
            except Exception:
                continue

        raise InstallError(t("software.python_error.uv_linux_failed"))

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        content = SHIM_SH_TEMPLATE.format(fallback=fallback_bin)
        for name in ("python3", "python"):
            shim = sdir / name
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        block = (
            f"\n{SHIM_MARKER_BEGIN}\n"
            f'export PATH="{shims_path}:$PATH"\n'
            f"{SHIM_MARKER_END}\n"
        )
        for rc in (
            Path.home() / ".bashrc",
            Path.home() / ".zshrc",
            Path.home() / ".profile",
            Path.home() / ".bash_profile",
        ):
            if not rc.exists():
                continue
            text = rc.read_text(encoding="utf-8")
            if SHIM_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

        try:
            if hasattr(os, "getuid") and os.getuid() == 0:
                pd = Path(PROFILE_D_SHIM_FILE)
                pd.write_text(f'export PATH="{shims_path}:$PATH"\n', encoding="utf-8")
                pd.chmod(0o644)
        except Exception:
            pass

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()

        for name in ("python3", "python"):
            shim = sdir / name
            if shim.exists():
                shim.unlink()
        try:
            sdir.rmdir()
        except Exception:
            pass

        for rc in (
            Path.home() / ".bashrc",
            Path.home() / ".zshrc",
            Path.home() / ".profile",
            Path.home() / ".bash_profile",
        ):
            if not rc.exists():
                continue
            try:
                lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
                out, skip = [], False
                for line in lines:
                    if line.strip() == SHIM_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == SHIM_MARKER_END:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

        try:
            pd = Path(PROFILE_D_SHIM_FILE)
            if pd.exists():
                pd.unlink()
        except Exception:
            pass

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(":"))

    # ─── symlink / PATH ───────────────────────────────────────────────────────

    def apply_version_link(self, new_bin: str) -> None:
        symlink_path, has_root = self._symlink_target()
        from .constants import SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END
        link = Path(symlink_path)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(new_bin, symlink_path)
        if not has_root:
            self._ensure_local_bin_in_path(SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END)

    def restore_original(
        self,
        symlink_path: str,
        original_target: str | None,
        had_local_bin_path: bool,
    ) -> None:
        from .constants import SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END
        link = Path(symlink_path)
        if link.is_symlink() or link.exists():
            link.unlink(missing_ok=True)
        if original_target and Path(original_target).exists():
            try:
                os.symlink(original_target, symlink_path)
            except Exception:
                pass
        if had_local_bin_path:
            self._remove_path_block(SYMLINK_MARKER_BEGIN, SYMLINK_MARKER_END)

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
        from core.runner import which, run
        for cmd in ("python3", "python"):
            if which(cmd):
                try:
                    result = run([cmd, "--version"], capture=True, check=False)
                    line = result.stdout.strip() or result.stderr.strip()
                    if "Python" in line:
                        return line.split()[-1]
                except Exception:
                    pass
        return None

    # ─── 内部工具 ─────────────────────────────────────────────────────────────

    def _symlink_target(self) -> tuple[str, bool]:
        has_root = (os.getuid() == 0) if hasattr(os, "getuid") else False
        if has_root:
            return "/usr/local/bin/python3", True
        return str(Path.home() / ".local" / "bin" / "python3"), False

    def _get_original_symlink_target(self, symlink_path: str) -> str | None:
        try:
            if os.path.islink(symlink_path):
                return os.readlink(symlink_path)
            if os.path.exists(symlink_path):
                return symlink_path
        except Exception:
            pass
        return None

    def _ensure_local_bin_in_path(self, begin_marker: str, end_marker: str) -> None:
        local_bin = str(Path.home() / ".local" / "bin")
        block = (
            f"\n{begin_marker}\n"
            f'export PATH="{local_bin}:$PATH"\n'
            f"{end_marker}\n"
        )
        for rc in (Path.home() / ".bashrc", Path.home() / ".zshrc"):
            if not rc.exists():
                continue
            content = rc.read_text(encoding="utf-8")
            if begin_marker not in content:
                rc.write_text(content + block, encoding="utf-8")

    def _remove_path_block(self, begin_marker: str, end_marker: str) -> None:
        for rc in (Path.home() / ".bashrc", Path.home() / ".zshrc"):
            if not rc.exists():
                continue
            try:
                lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
                out, skip = [], False
                for line in lines:
                    if line.strip() == begin_marker:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == end_marker:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass
