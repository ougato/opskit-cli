"""Redis macOS 平台驱动：源码 tar.gz make 编译 + shim sh + PATH 注入（Xcode CLI tools 提供 make/gcc）"""
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
    REDIS_PATH_MARKER_BEGIN,
    REDIS_PATH_MARKER_END,
    SHIM_REDIS_SH_TEMPLATE,
)


class DarwinDriver(PlatformDriver):

    # ─── 二进制安装（源码编译）────────────────────────────────────────────────

    def install_binary(self, version: str, src: Path) -> str:
        """从源码 tar.gz make 编译并安装到版本专属目录，返回 bin 目录路径字符串"""
        if not shutil.which("make"):
            raise InstallError(t("software.redis_error.src_need_build_tools"))
        from .common import redis_version_dir, redis_bin_dir
        dest = redis_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        bin_d = redis_bin_dir(version)
        bin_d.mkdir(parents=True, exist_ok=True)

        import tempfile
        with tempfile.TemporaryDirectory(prefix="opskit-redis-src-", ignore_cleanup_errors=True) as tmp:
            try:
                with tarfile.open(str(src), "r:gz") as tf:
                    tf.extractall(tmp)
            except Exception as e:
                raise InstallError(t("software.redis_error.src_extract_failed", error=e)) from e
            src_dirs = [d for d in Path(tmp).iterdir() if d.is_dir() and d.name.startswith("redis")]
            if not src_dirs:
                raise InstallError(t("software.redis_error.src_bad_structure"))
            src_dir = src_dirs[0]
            try:
                subprocess.run(
                    ["make", f"PREFIX={dest}", "install"],
                    cwd=str(src_dir), check=True, capture_output=True
                )
            except subprocess.CalledProcessError as e:
                raise InstallError(
                    t("software.redis_error.src_make_failed", error=e.stderr.decode(errors='replace')[:300])
                ) from e

        for binary in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark",
                       "redis-check-aof", "redis-check-rdb"):
            installed = dest / "bin" / binary
            target = bin_d / binary
            if installed.exists() and not target.exists():
                shutil.copy2(str(installed), str(target))
                target.chmod(0o755)

        redis_server = bin_d / "redis-server"
        if not redis_server.exists():
            raise InstallError(t("software.redis_error.macos_bad_structure", version=version))
        return str(bin_d)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        for name in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
            shim = sdir / name
            content = SHIM_REDIS_SH_TEMPLATE.format(binary=name, fallback=fallback_bin)
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(":"):
            os.environ["PATH"] = shims_path + ":" + cur_path

        block = (
            f"\n{REDIS_PATH_MARKER_BEGIN}\n"
            f'export PATH="{shims_path}:$PATH"\n'
            f"{REDIS_PATH_MARKER_END}\n"
        )
        for rc in (
            Path.home() / ".zshrc",
            Path.home() / ".bash_profile",
            Path.home() / ".bashrc",
            Path.home() / ".profile",
        ):
            if not rc.exists():
                continue
            text = rc.read_text(encoding="utf-8")
            if REDIS_PATH_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        for name in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
            shim = sdir / name
            if shim.exists():
                shim.unlink()
        try:
            sdir.rmdir()
        except Exception:
            pass
        for rc in (
            Path.home() / ".zshrc",
            Path.home() / ".bash_profile",
            Path.home() / ".bashrc",
            Path.home() / ".profile",
        ):
            if not rc.exists():
                continue
            try:
                lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
                out, skip = [], False
                for line in lines:
                    if line.strip() == REDIS_PATH_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == REDIS_PATH_MARKER_END:
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
        bin_path = Path(bin_dir)
        usr_local_bin = Path("/usr/local/bin")
        if os.access(str(usr_local_bin), os.W_OK):
            for name in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
                src = bin_path / name
                if not src.exists():
                    continue
                dest = usr_local_bin / name
                try:
                    if dest.is_symlink() or dest.exists():
                        dest.unlink()
                    dest.symlink_to(src)
                except Exception:
                    pass
        from .common import shim_dir as _sd
        shims = str(_sd())
        cur_path = os.environ.get("PATH", "")
        if shims not in cur_path.split(":"):
            os.environ["PATH"] = shims + ":" + cur_path

    def restore_original(self) -> None:
        from .common import redis_versions_dir as _rvd
        usr_local_bin = Path("/usr/local/bin")
        for name in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
            dest = usr_local_bin / name
            try:
                if dest.is_symlink():
                    target = dest.resolve()
                    if str(_rvd()) in str(target):
                        dest.unlink()
            except Exception:
                pass

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, redis_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            redis_bin = redis_bin_dir(active) / "redis-server"
            if redis_bin.exists():
                return active
        redis_cmd = shutil.which("redis-server")
        if redis_cmd:
            try:
                r = subprocess.run(
                    [redis_cmd, "--version"], capture_output=True, text=True, timeout=5
                )
                line = r.stdout.strip()
                for part in line.split():
                    if part.startswith("v="):
                        return part[2:]
                    p = part.lstrip("v")
                    if p and p[0].isdigit():
                        return p.rstrip(",")
            except Exception:
                pass
        return None
