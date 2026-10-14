"""Redis Linux 平台驱动：deb 解压提取二进制（或源码编译 fallback）+ shim sh + PATH 注入"""
from __future__ import annotations

from core.archive_reader import open_tar

import os
import shutil
import subprocess
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import (
    REDIS_PATH_MARKER_BEGIN,
    REDIS_PATH_MARKER_END,
    SHIM_REDIS_SH_TEMPLATE,
    PROFILE_D_REDIS_FILE,
)

_DEB_BINARIES = frozenset(("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark",
                           "redis-check-aof", "redis-check-rdb"))


def _resolve_deb_aliases(bin_dir, pending):
    links = {}
    for name, target, hard in pending:
        if name in links or os.path.lexists(bin_dir / name):
            raise InstallError("Redis package aliases conflict")
        links[name] = target, hard
    resolved = {}
    for name in links:
        current, trail, seen = name, [], set()
        while current in links and current not in resolved:
            if current in seen:
                raise InstallError("Redis package aliases are cyclic")
            seen.add(current)
            trail.append(current)
            current = links[current][0]
        terminal = resolved.get(current, current)
        binary = bin_dir / terminal
        if not binary.is_file() or binary.is_symlink():
            raise InstallError("Redis package alias target is missing")
        for node in trail:
            resolved[node] = terminal
    for name, (_, hard) in links.items():
        if hard:
            os.link(bin_dir / resolved[name], bin_dir / name)
        else:
            os.symlink(resolved[name], bin_dir / name)


class LinuxDriver(PlatformDriver):

    # ─── 二进制安装 ────────────────────────────────────────────────────────────

    def install_binary(self, version: str, src) -> str:
        """
        src 可能是 Path 或 list[Path]（Linux 返回列表）。
        - .deb → 有界解析 + tar 白名单提取 usr/bin/redis-server, redis-cli（多个 deb 合并）
        - .tar.gz → make 编译
        返回 bin 目录路径字符串。
        """
        from .common import redis_version_dir, redis_bin_dir
        from software._shared.archive_install import staged_directory
        dest = redis_version_dir(version)
        srcs = src if isinstance(src, list) else [src]
        try:
            with staged_directory(dest, prefix=".opskit-redis-") as staging:
                bin_d = staging / "bin"
                bin_d.mkdir(parents=True)
                pending = []
                for source in srcs:
                    if source.suffix == ".deb":
                        pending.extend(self._install_from_deb(source, bin_d))
                    else:
                        self._install_from_src(source, bin_d)
                _resolve_deb_aliases(bin_d, pending)
                if not all((bin_d / binary).is_file() for binary in ("redis-server", "redis-cli")):
                    raise InstallError(t("software.redis_error.linux_bad_structure", version=version))
        except FileExistsError as error:
            raise InstallError("Redis version directory already exists; refusing to overwrite") from error
        except InstallError:
            raise
        except Exception as error:
            raise InstallError("Redis installation preparation or publication failed") from error
        return str(redis_bin_dir(version))

    def _install_from_deb(self, deb: Path, bin_d: Path):
        """Map only exact usr/bin members; aliases resolve after all packages merge."""
        import tempfile
        import posixpath
        from core.debian import extract_data_tar, open_data_tar
        from core.archive import extract_tar_files
        pending = []
        with tempfile.TemporaryDirectory(prefix="opskit-redis-deb-") as work:
            try:
                data_tar = extract_data_tar(deb, Path(work))

                def select(name, member):
                    parts = name.split("/")
                    if len(parts) != 3 or parts[:2] != ["usr", "bin"] or parts[-1] not in _DEB_BINARIES:
                        return None
                    base = parts[-1]
                    if member.issym() or member.islnk():
                        raw = member.linkname.replace("\\", "/")
                        if not raw or raw.startswith("/") or ":" in raw:
                            raise ValueError("unsafe Redis package alias")
                        linked = posixpath.normpath(posixpath.join("usr/bin", raw) if member.issym() else raw)
                        target_parts = linked.split("/")
                        if len(target_parts) != 3 or target_parts[:2] != ["usr", "bin"] or target_parts[-1] not in _DEB_BINARIES:
                            raise ValueError("Redis package alias target is not allowlisted")
                        pending.append((base, target_parts[-1], member.islnk()))
                        return None
                    return base

                with open_data_tar(data_tar) as archive:
                    extract_tar_files(archive, bin_d, select)
            except InstallError:
                raise
            except Exception as error:
                raise InstallError(t("software.redis_error.deb_extract_failed", error="invalid Redis package payload")) from error
        return pending

    def _install_from_src(self, tarball: Path, bin_d: Path) -> None:
        """从源码 tar.gz make 编译"""
        if not shutil.which("make") or not shutil.which("gcc"):
            raise InstallError(t("software.redis_error.src_need_build_tools"))
        import tempfile
        with tempfile.TemporaryDirectory(prefix="opskit-redis-src-", ignore_cleanup_errors=True) as tmp:
            try:
                with open_tar(str(tarball), "r:gz") as tf:
                    from core.archive import extract_tar
                    extract_tar(tf, tmp)
            except Exception as e:
                raise InstallError(t("software.redis_error.src_extract_failed", error=e)) from e
            src_dirs = [d for d in Path(tmp).iterdir() if d.is_dir() and d.name.startswith("redis")]
            if len(src_dirs) != 1:
                raise InstallError(t("software.redis_error.src_bad_structure"))
            src_dir = src_dirs[0]
            try:
                from core.process import capture
                result = capture(["make", "-j4"], cwd=src_dir, timeout=1800,
                                 max_bytes=1024 * 1024, stdin=subprocess.DEVNULL)
                if result.returncode != 0:
                    raise InstallError(t("software.redis_error.src_make_failed", error="build command failed"))
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                raise InstallError(t("software.redis_error.src_make_failed", error="build command failed or timed out")) from error
            for binary in _DEB_BINARIES:
                built = src_dir / "src" / binary
                target = bin_d / binary
                if built.is_file():
                    if target.exists():
                        raise InstallError("Redis build outputs conflict")
                    shutil.copy2(built, target)
                    target.chmod(0o755)

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
            Path.home() / ".bashrc",
            Path.home() / ".zshrc",
            Path.home() / ".profile",
            Path.home() / ".bash_profile",
        ):
            if not rc.exists():
                continue
            text = rc.read_text(encoding="utf-8")
            if REDIS_PATH_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

        try:
            if hasattr(os, "getuid") and os.getuid() == 0:
                pd = Path(PROFILE_D_REDIS_FILE)
                pd.write_text(f'export PATH="{shims_path}:$PATH"\n', encoding="utf-8")
                pd.chmod(0o644)
        except Exception:
            pass

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
                    if line.strip() == REDIS_PATH_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == REDIS_PATH_MARKER_END:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass
        try:
            pd = Path(PROFILE_D_REDIS_FILE)
            if pd.exists():
                pd.unlink()
        except Exception:
            pass

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(":"))

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        bin_path = Path(bin_dir)
        if hasattr(os, "getuid") and os.getuid() == 0:
            for name in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
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
        if not (hasattr(os, "getuid") and os.getuid() == 0):
            return
        from .common import redis_versions_dir as _rvd
        for name in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
            dest = Path("/usr/local/bin") / name
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
