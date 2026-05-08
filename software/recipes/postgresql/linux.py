"""PostgreSQL Linux 平台驱动：tar.gz 解压、shim sh、shell rc PATH 注入（对齐 mongodb/linux.py）"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import (
    PGSQL_PATH_MARKER_BEGIN,
    PGSQL_PATH_MARKER_END,
    SHIM_PGSQL_SH_TEMPLATE,
    PROFILE_D_PGSQL_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        Linux 路线：deb 包已由 download_pgsql_tarball() 直接解压到版本目录。
        此处只做目录完整性验证，返回 bin 目录路径字符串。
        """
        from .common import pgsql_bin_dir
        bin_dir = pgsql_bin_dir(version)
        if not (bin_dir / "psql").exists():
            raise InstallError(t("software.postgresql_error.linux_bad_structure", version=version))
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        content = SHIM_PGSQL_SH_TEMPLATE.format(fallback=fallback_bin)
        for name in ("psql", "pg_ctl", "pg_dump", "pg_restore", "postgres"):
            shim = sdir / name
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(":"):
            os.environ["PATH"] = shims_path + ":" + cur_path

        block = (
            f"\n{PGSQL_PATH_MARKER_BEGIN}\n"
            f'export PATH="{shims_path}:$PATH"\n'
            f"{PGSQL_PATH_MARKER_END}\n"
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
            if PGSQL_PATH_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

        try:
            if hasattr(os, "getuid") and os.getuid() == 0:
                pd = Path(PROFILE_D_PGSQL_FILE)
                pd.write_text(f'export PATH="{shims_path}:$PATH"\n', encoding="utf-8")
                pd.chmod(0o644)
        except Exception:
            pass

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        for name in ("psql", "pg_ctl", "pg_dump", "pg_restore", "postgres"):
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
                    if line.strip() == PGSQL_PATH_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == PGSQL_PATH_MARKER_END:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

        try:
            pd = Path(PROFILE_D_PGSQL_FILE)
            if pd.exists():
                pd.unlink()
        except Exception:
            pass

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """
        root 时在 /usr/local/bin 创建/更新 psql/pg_ctl symlink，立即全局生效。
        非 root 时依赖 shim，注入当前进程 PATH。
        """
        bin_path = Path(bin_dir)
        if hasattr(os, "getuid") and os.getuid() == 0:
            for name in ("psql", "pg_ctl", "pg_dump", "pg_restore", "postgres"):
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
        """卸载时删除 /usr/local/bin 下 opskit 创建的 symlink"""
        if not (hasattr(os, "getuid") and os.getuid() == 0):
            return
        from .common import pgsql_versions_dir as _pvd
        for name in ("psql", "pg_ctl", "pg_dump", "pg_restore", "postgres"):
            dest = Path("/usr/local/bin") / name
            try:
                if dest.is_symlink():
                    target = dest.resolve()
                    if str(_pvd()) in str(target):
                        dest.unlink()
            except Exception:
                pass

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect(self) -> str | None:
        from .common import load_snapshot, pgsql_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            psql_bin = pgsql_bin_dir(active) / "psql"
            if psql_bin.exists():
                return active
        psql_cmd = shutil.which("psql")
        if psql_cmd:
            try:
                r = subprocess.run(
                    [psql_cmd, "--version"], capture_output=True, text=True, timeout=5
                )
                for part in r.stdout.strip().split():
                    if part and part[0].isdigit():
                        return part.rstrip(",")
            except Exception:
                pass
        return None
