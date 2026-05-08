"""MySQL macOS 平台驱动：tar.gz 解压、shim sh、shell rc PATH 注入（对齐 linux.py）"""
from __future__ import annotations

import os
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .common import extract_tarball, detect_mysql_version
from .constants import (
    MYSQL_PATH_MARKER_BEGIN,
    MYSQL_PATH_MARKER_END,
    SHIM_MYSQL_SH_TEMPLATE,
)


class DarwinDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 tar.gz 解压到版本专属目录：~/.opskit/mysql/mysql{version}/
        返回 bin 目录路径字符串。
        """
        from .common import mysql_version_dir, mysql_bin_dir
        dest = mysql_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        extract_tarball(tarball, dest, version)
        bin_dir = mysql_bin_dir(version)
        if not (bin_dir / "mysql").exists():
            raise InstallError(t("software.mysql_error.macos_bad_structure", version=version))
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        for name in ("mysql", "mysqld", "mysqladmin", "mysqldump"):
            shim = sdir / name
            content = SHIM_MYSQL_SH_TEMPLATE.format(binary=name, fallback=fallback_bin)
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(":"):
            os.environ["PATH"] = shims_path + ":" + cur_path

        block = (
            f"\n{MYSQL_PATH_MARKER_BEGIN}\n"
            f'export PATH="{shims_path}:$PATH"\n'
            f"{MYSQL_PATH_MARKER_END}\n"
        )
        for rc in (
            Path.home() / ".bashrc",
            Path.home() / ".zshrc",
            Path.home() / ".profile",
            Path.home() / ".bash_profile",
            Path.home() / ".zprofile",
        ):
            if not rc.exists():
                continue
            text = rc.read_text(encoding="utf-8")
            if MYSQL_PATH_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()

        for name in ("mysql", "mysqld", "mysqladmin", "mysqldump"):
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
            Path.home() / ".zprofile",
        ):
            if not rc.exists():
                continue
            try:
                lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
                out, skip = [], False
                for line in lines:
                    if line.strip() == MYSQL_PATH_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == MYSQL_PATH_MARKER_END:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(":"))

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """
        /usr/local/bin symlink（需要 /usr/local/bin 可写，通常 macOS 下有权限）。
        非 root 时依赖 shim，注入当前进程 PATH。
        """
        bin_path = Path(bin_dir)
        for name in ("mysql", "mysqld", "mysqladmin", "mysqldump"):
            src = bin_path / name
            if not src.exists():
                continue
            dest = Path("/usr/local/bin") / name
            try:
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                dest.symlink_to(src)
            except Exception:
                pass
        from .common import shim_dir as _shim_dir
        shims = str(_shim_dir())
        cur_path = os.environ.get("PATH", "")
        if shims not in cur_path.split(":"):
            os.environ["PATH"] = shims + ":" + cur_path

    def restore_original(self) -> None:
        from .common import mysql_versions_dir as _mvd
        for name in ("mysql", "mysqld", "mysqladmin", "mysqldump"):
            dest = Path("/usr/local/bin") / name
            try:
                if dest.is_symlink():
                    target = dest.resolve()
                    if str(_mvd()) in str(target):
                        dest.unlink()
            except Exception:
                pass

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        return detect_mysql_version("mysql")
