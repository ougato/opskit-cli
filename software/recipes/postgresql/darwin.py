"""PostgreSQL macOS 平台驱动：tarball/zip 解压、shim sh、shell rc PATH 注入（对齐 linux.py）"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import (
    PGSQL_PATH_MARKER_BEGIN,
    PGSQL_PATH_MARKER_END,
    SHIM_PGSQL_SH_TEMPLATE,
)


class DarwinDriver(PlatformDriver):

    # ─── tarball/zip 安装 ────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 tarball/zip 解压到版本专属目录：~/.opskit/postgresql/postgresql{version}/
        EDB 下载为 zip，theseus-rs 为 tar.gz，自动识别。
        返回 bin 目录路径字符串。
        """
        from .common import pgsql_version_dir, pgsql_bin_dir
        dest = pgsql_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)

        name_lower = str(tarball).lower()
        try:
            if name_lower.endswith(".zip"):
                with zipfile.ZipFile(str(tarball), "r") as zf:
                    for member in zf.infolist():
                        name = member.filename
                        parts = name.split("/", 1)
                        if len(parts) < 2 or not parts[1]:
                            continue
                        rel = parts[1]
                        target = dest / rel
                        if name.endswith("/"):
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src, open(target, "wb") as out:
                                shutil.copyfileobj(src, out)
            else:
                with tarfile.open(str(tarball), "r:gz") as tf:
                    for member in tf.getmembers():
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
            raise InstallError(t("software.postgresql_error.macos_extract_failed", version=version, error=e)) from e

        bin_dir = pgsql_bin_dir(version)
        if not (bin_dir / "psql").exists():
            raise InstallError(t("software.postgresql_error.macos_bad_structure", version=version))
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
            Path.home() / ".zshrc",
            Path.home() / ".bashrc",
            Path.home() / ".profile",
            Path.home() / ".bash_profile",
            Path.home() / ".zprofile",
        ):
            if not rc.exists():
                continue
            text = rc.read_text(encoding="utf-8")
            if PGSQL_PATH_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

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
            Path.home() / ".zshrc",
            Path.home() / ".bashrc",
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
                    if line.strip() == PGSQL_PATH_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == PGSQL_PATH_MARKER_END:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """创建 active symlink 指向激活版本目录，注入当前进程 PATH"""
        from .common import active_link, shim_dir as _shim_dir
        link = active_link()
        version_dir = str(Path(bin_dir).parent)
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(version_dir)
        except Exception:
            pass
        shims = str(_shim_dir())
        cur_path = os.environ.get("PATH", "")
        if shims not in cur_path.split(":"):
            os.environ["PATH"] = shims + ":" + cur_path

    def restore_original(self) -> None:
        from .common import active_link
        link = active_link()
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
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
