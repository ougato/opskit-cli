"""Linux 平台驱动：tar.xz/tar.gz 解压、shim sh、shell rc PATH 注入"""
from __future__ import annotations

from core.archive_reader import open_tar

import shutil
import subprocess
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from software._shared import shell_path
from .driver import PlatformDriver
from .constants import (
    NODE_PATH_MARKER_BEGIN,
    NODE_PATH_MARKER_END,
    SHIM_NODE_SH_TEMPLATE,
    PROFILE_D_NODE_FILE,
)

# node / npm / npx 三个 shim 命令
_SHIM_CMDS = ("node", "npm", "npx")


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 tar.xz / tar.gz 解压到版本专属目录：~/.opskit/nodejs/nodeX.Y.Z/
        Node 官方包内层目录格式：node-vX.Y.Z-linux-arch/
        返回 bin 目录路径字符串。
        """
        from .common import node_version_dir, node_bin_dir
        from core.archive import extract_tar
        from software._shared.archive_install import staged_directory
        dest = node_version_dir(version)
        mode = "r:xz" if str(tarball).lower().endswith(".tar.xz") else "r:gz"
        try:
            with staged_directory(dest, prefix=".opskit-nodejs-") as staging:
                with open_tar(str(tarball), mode) as archive:
                    extract_tar(archive, staging, strip_components=1, allow_symlinks=True)
                for binary in ('node', 'npm'):
                    if not (staging / "bin" / binary).is_file():
                        raise InstallError(t("software.nodejs_error.bad_structure", version=version, file="bin/" + binary))
        except FileExistsError as error:
            raise InstallError("nodejs version directory already exists; refusing to overwrite") from error
        except InstallError:
            raise
        except Exception as error:
            raise InstallError(t("software.nodejs_error.extract_failed", version=version, error=error)) from error
        return str(node_bin_dir(version))

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        for cmd in _SHIM_CMDS:
            # fallback：用系统对应命令（node/npm/npx）
            fb = shutil.which(cmd) or cmd
            content = SHIM_NODE_SH_TEMPLATE.format(cmd=cmd, fallback=fb)
            shim = sdir / cmd
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        # 立即注入当前进程 PATH
        shell_path.prepend_process_path(shims_path)
        shell_path.inject_rc_path(
            shims_path, NODE_PATH_MARKER_BEGIN, NODE_PATH_MARKER_END, PROFILE_D_NODE_FILE
        )

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()

        for cmd in _SHIM_CMDS:
            shim = sdir / cmd
            if shim.exists():
                shim.unlink()
        try:
            sdir.rmdir()
        except Exception:
            pass

        shell_path.remove_rc_path(NODE_PATH_MARKER_BEGIN, NODE_PATH_MARKER_END, PROFILE_D_NODE_FILE)

    def shim_active(self) -> bool:
        from .common import shim_dir
        return shell_path.process_path_contains(str(shim_dir()))

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """
        优先在 /usr/local/bin 创建/更新 node/npm/npx symlink（root 时立即全局生效）。
        非 root 时依赖 shim，同时注入当前进程 PATH 让 opskit 子进程立即可用。
        """
        from .common import node_versions_dir
        shell_path.link_into_system_bin(bin_dir, _SHIM_CMDS, managed_root=node_versions_dir())
        from .common import shim_dir as _shim_dir
        shell_path.prepend_process_path(str(_shim_dir()))

    def restore_original(self) -> None:
        """卸载时删除 /usr/local/bin 下 opskit 创建的 symlink"""
        from .common import node_versions_dir as _nvd
        shell_path.unlink_system_bin(_SHIM_CMDS, _nvd())

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, node_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            node_bin = node_bin_dir(active) / "node"
            if node_bin.exists():
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
