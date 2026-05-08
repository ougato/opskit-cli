"""Redis Linux 平台驱动：deb 解压提取二进制（或源码编译 fallback）+ shim sh + PATH 注入"""
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
    PROFILE_D_REDIS_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── 二进制安装 ────────────────────────────────────────────────────────────

    def install_binary(self, version: str, src) -> str:
        """
        src 可能是 Path 或 list[Path]（Linux 返回列表）。
        - .deb → ar x + tar 提取 usr/bin/redis-server, redis-cli（多个 deb 合并）
        - .tar.gz → make 编译
        返回 bin 目录路径字符串。
        """
        from .common import redis_version_dir, redis_bin_dir
        dest = redis_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        bin_d = redis_bin_dir(version)
        bin_d.mkdir(parents=True, exist_ok=True)

        srcs: list[Path] = src if isinstance(src, list) else [src]
        for s in srcs:
            suffix = "".join(s.suffixes)
            if suffix.endswith(".deb"):
                self._install_from_deb(s, bin_d)
            else:
                self._install_from_src(s, dest, bin_d)

        # Debian 包里 redis-server 是 symlink → redis-check-rdb
        # 若 symlink 提取后仍缺失，用 redis-check-rdb 复制兜底
        redis_server = bin_d / "redis-server"
        if not redis_server.exists():
            fallback = bin_d / "redis-check-rdb"
            if fallback.exists():
                import shutil as _sh
                _sh.copy2(str(fallback), str(redis_server))
                redis_server.chmod(0o755)

        if not redis_server.exists():
            raise InstallError(t("software.redis_error.linux_bad_structure", version=version))
        return str(bin_d)

    def _install_from_deb(self, deb: Path, bin_d: Path) -> None:
        """从 deb 包用 ar x + tar 提取 redis-server / redis-cli"""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="opskit-redis-deb-", ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            try:
                subprocess.run(["ar", "x", str(deb)], cwd=tmp, check=True, capture_output=True)
            except Exception as e:
                raise InstallError(t("software.redis_error.deb_unpack_failed", error=e)) from e
            data_tar = None
            for name in ("data.tar.xz", "data.tar.gz", "data.tar.zst", "data.tar.bz2", "data.tar"):
                candidate = tmp_path / name
                if candidate.exists():
                    data_tar = candidate
                    break
            if data_tar is None:
                raise InstallError(t("software.redis_error.deb_no_data_tar"))
            _BINARIES = frozenset((
                "redis-server", "redis-cli", "redis-sentinel",
                "redis-benchmark", "redis-check-aof", "redis-check-rdb",
            ))
            try:
                with tarfile.open(str(data_tar)) as tf:
                    _pending_links: list[tuple[str, str]] = []
                    for member in tf.getmembers():
                        norm = member.name.lstrip("./")
                        base = Path(norm).name
                        if base not in _BINARIES:
                            continue
                        # 只提取 usr/bin/ 下的二进制，排除 bash-completion 等同名文件
                        if not norm.startswith("usr/bin/"):
                            continue
                        target = bin_d / base
                        if member.issym() or member.islnk():
                            # 记录软/硬链接，等普通文件提取完再处理
                            _pending_links.append((base, Path(member.linkname).name))
                            continue
                        src_f = tf.extractfile(member)
                        if src_f:
                            target.write_bytes(src_f.read())
                            target.chmod(0o755)
                    # 处理符号链接（redis-check-rdb → redis-server 等）
                    for link_name, link_target in _pending_links:
                        target = bin_d / link_name
                        if not target.exists():
                            real = bin_d / link_target
                            if real.exists():
                                import os as _os
                                _os.symlink(str(real), str(target))
                            else:
                                # 源文件也不在 bin_d，复制 redis-server 充当
                                fallback = bin_d / "redis-server"
                                if fallback.exists():
                                    import shutil as _sh
                                    _sh.copy2(str(fallback), str(target))
                                    target.chmod(0o755)
            except InstallError:
                raise
            except Exception as e:
                raise InstallError(t("software.redis_error.deb_extract_failed", error=e)) from e

    def _install_from_src(self, tarball: Path, dest: Path, bin_d: Path) -> None:
        """从源码 tar.gz make 编译"""
        if not shutil.which("make") or not shutil.which("gcc"):
            raise InstallError(t("software.redis_error.src_need_build_tools"))
        import tempfile
        with tempfile.TemporaryDirectory(prefix="opskit-redis-src-", ignore_cleanup_errors=True) as tmp:
            try:
                with tarfile.open(str(tarball), "r:gz") as tf:
                    tf.extractall(tmp)
            except Exception as e:
                raise InstallError(t("software.redis_error.src_extract_failed", error=e)) from e
            src_dirs = [d for d in Path(tmp).iterdir() if d.is_dir() and d.name.startswith("redis")]
            if not src_dirs:
                raise InstallError(t("software.redis_error.src_bad_structure"))
            src_dir = src_dirs[0]
            try:
                subprocess.run(
                    ["make", "-j4"],
                    cwd=str(src_dir), check=True, capture_output=True
                )
            except subprocess.CalledProcessError as e:
                raise InstallError(t("software.redis_error.src_make_failed", error=e.stderr.decode(errors='replace')[:200])) from e
        for binary in ("redis-server", "redis-cli"):
            installed = dest / "bin" / binary
            target = bin_d / binary
            if installed.exists() and not target.exists():
                shutil.copy2(str(installed), str(target))
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
