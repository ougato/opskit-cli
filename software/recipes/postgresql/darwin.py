"""PostgreSQL macOS 平台驱动：tarball/zip 解压、shim sh、shell rc PATH 注入（对齐 linux.py）"""
from __future__ import annotations

from core.archive_reader import open_tar, open_zip

import os
import shutil
import subprocess
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
        from core.archive import extract_tar, extract_zip
        from software._shared.archive_install import staged_directory
        dest = pgsql_version_dir(version)
        try:
            with staged_directory(dest, prefix=".opskit-pgsql-") as staging:
                # The download race writes ZIP and tar.gz responses to the same
                # destination. Select by content using one open file handle.
                with tarball.open("rb") as source:
                    signature = source.read(4)
                    source.seek(0)
                    if signature in (b"PK\x03\x04", b"PK\x05\x06"):
                        with open_zip(fileobj=source) as archive:
                            extract_zip(archive, staging, strip_components=1, preserve_mode=True)
                    else:
                        with open_tar(fileobj=source, mode="r:gz") as archive:
                            extract_tar(archive, staging, strip_components=1, allow_symlinks=True)
                if not (staging / "bin/psql").is_file():
                    raise InstallError(t("software.postgresql_error.macos_bad_structure", version=version))
        except FileExistsError as error:
            raise InstallError("PostgreSQL version directory already exists; refusing to overwrite") from error
        except InstallError:
            raise
        except Exception as error:
            raise InstallError(t("software.postgresql_error.macos_extract_failed", version=version, error=error)) from error
        return str(pgsql_bin_dir(version))

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
