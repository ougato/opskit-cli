"""Linux 平台驱动：tar.gz 解压、shim sh、shell rc PATH 注入（对齐 golang/linux.py）"""
from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from software._shared import shell_path
from .driver import PlatformDriver
from .constants import (
    MONGO_PATH_MARKER_BEGIN,
    MONGO_PATH_MARKER_END,
    SHIM_MONGO_SH_TEMPLATE,
    PROFILE_D_MONGO_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 tar.gz 解压到版本专属目录：~/.opskit/mongodb/mongodb{version}/
        返回 bin 目录路径字符串。
        """
        from .common import mongo_version_dir, mongo_bin_dir
        dest = mongo_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(str(tarball), "r:gz") as tf:
                for member in tf.getmembers():
                    # tarball 内顶层目录名如 mongodb-linux-x86_64-8.0.4
                    parts = member.name.split("/", 1)
                    if len(parts) < 2 or not parts[1]:
                        continue
                    rel = parts[1]
                    target = dest / rel
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tf.extractfile(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                        if member.mode & 0o111:
                            target.chmod(target.stat().st_mode | 0o111)
        except Exception as e:
            raise InstallError(t("software.mongodb_error.extract_failed", version=version, error=e)) from e

        bin_dir = mongo_bin_dir(version)
        if not (bin_dir / "mongod").exists():
            raise InstallError(t("software.mongodb_error.bad_structure", version=version, file="bin/mongod"))
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        content = SHIM_MONGO_SH_TEMPLATE.format(fallback=fallback_bin)
        for name in ("mongod", "mongos"):
            shim = sdir / name
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        shell_path.prepend_process_path(shims_path)
        shell_path.inject_rc_path(
            shims_path, MONGO_PATH_MARKER_BEGIN, MONGO_PATH_MARKER_END, PROFILE_D_MONGO_FILE
        )

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()

        for name in ("mongod", "mongos"):
            shim = sdir / name
            if shim.exists():
                shim.unlink()
        try:
            sdir.rmdir()
        except Exception:
            pass

        shell_path.remove_rc_path(MONGO_PATH_MARKER_BEGIN, MONGO_PATH_MARKER_END, PROFILE_D_MONGO_FILE)

    def shim_active(self) -> bool:
        from .common import shim_dir
        return shell_path.process_path_contains(str(shim_dir()))

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """
        root 时在 /usr/local/bin 创建/更新 mongod/mongos symlink，立即全局生效。
        非 root 时依赖 shim，注入当前进程 PATH。
        """
        shell_path.link_into_system_bin(bin_dir, ("mongod", "mongos"))
        from .common import shim_dir as _shim_dir
        shell_path.prepend_process_path(str(_shim_dir()))

    def restore_original(self) -> None:
        """卸载时删除 /usr/local/bin 下 opskit 创建的 symlink"""
        from .common import mongo_versions_dir as _mvd
        shell_path.unlink_system_bin(("mongod", "mongos"), _mvd())

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, mongo_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            mongod_bin = mongo_bin_dir(active) / "mongod"
            if mongod_bin.exists():
                return active
        mongod_cmd = shutil.which("mongod")
        if mongod_cmd:
            try:
                r = subprocess.run(
                    [mongod_cmd, "--version"], capture_output=True, text=True, timeout=5
                )
                for part in r.stdout.strip().split():
                    p = part.lstrip("v") if part.startswith("v") else part
                    if p and p[0].isdigit():
                        return p.rstrip(",")
            except Exception:
                pass
        return None
