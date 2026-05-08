"""Linux 平台驱动：tar.gz 解压、shim sh、shell rc PATH 注入"""
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
    JAVA_PATH_MARKER_BEGIN,
    JAVA_PATH_MARKER_END,
    JAVA_SHIM_CMDS,
    SHIM_JAVA_SH_TEMPLATE,
    PROFILE_D_JAVA_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 tar.gz 解压到版本专属目录：~/.opskit/java/jdk{version}/
        Temurin 包内层目录格式：jdk-21.0.11+10/ 或 jdk-17.0.19+10/
        返回 bin 目录路径字符串。
        """
        from .common import java_version_dir, java_bin_dir
        dest = java_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(str(tarball), "r:gz") as tf:
                # 找出顶层目录前缀（如 "jdk-21.0.11+10/"）
                prefix = ""
                for member in tf.getmembers():
                    if "/" in member.name:
                        prefix = member.name.split("/")[0] + "/"
                        break
                    elif member.isdir():
                        prefix = member.name.rstrip("/") + "/"
                        break
                for member in tf.getmembers():
                    if prefix and not member.name.startswith(prefix):
                        continue
                    rel = member.name[len(prefix):]
                    if not rel:
                        continue
                    target = dest / rel
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tf.extractfile(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                        if member.mode & 0o111:
                            target.chmod(target.stat().st_mode | 0o111)
                    elif member.issym():
                        # 处理 symlink（Temurin 包中 legal/ 目录下含大量 symlink）
                        target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            if target.is_symlink() or target.exists():
                                target.unlink()
                            os.symlink(member.linkname, target)
                        except Exception:
                            pass
        except Exception as e:
            raise InstallError(t("software.java_error.extract_failed", version=version, error=e)) from e

        bin_d = java_bin_dir(version)
        if not (bin_d / "java").exists():
            raise InstallError(t("software.java_error.bad_structure", version=version, file="bin/java"))
        return str(bin_d)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        for cmd in JAVA_SHIM_CMDS:
            fb = shutil.which(cmd) or cmd
            content = SHIM_JAVA_SH_TEMPLATE.format(cmd=cmd, fallback=fb)
            shim = sdir / cmd
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        # 立即注入当前进程 PATH
        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(":"):
            os.environ["PATH"] = shims_path + ":" + cur_path

        block = (
            f"\n{JAVA_PATH_MARKER_BEGIN}\n"
            f'export PATH="{shims_path}:$PATH"\n'
            f"{JAVA_PATH_MARKER_END}\n"
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
            if JAVA_PATH_MARKER_BEGIN not in text:
                rc.write_text(text + block, encoding="utf-8")

        try:
            if hasattr(os, "getuid") and os.getuid() == 0:
                pd = Path(PROFILE_D_JAVA_FILE)
                pd.write_text(f'export PATH="{shims_path}:$PATH"\n', encoding="utf-8")
                pd.chmod(0o644)
        except Exception:
            pass

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()

        for cmd in JAVA_SHIM_CMDS:
            shim = sdir / cmd
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
                    if line.strip() == JAVA_PATH_MARKER_BEGIN:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == JAVA_PATH_MARKER_END:
                        skip = False
                rc.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

        try:
            pd = Path(PROFILE_D_JAVA_FILE)
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
        """
        优先在 /usr/local/bin 创建/更新 java/javac/jar symlink（root 时立即全局生效）。
        非 root 时依赖 shim，同时注入当前进程 PATH。
        """
        bin_path = Path(bin_dir)
        if hasattr(os, "getuid") and os.getuid() == 0:
            for cmd in JAVA_SHIM_CMDS:
                src = bin_path / cmd
                if not src.exists():
                    continue
                dest = Path("/usr/local/bin") / cmd
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
        for cmd in JAVA_SHIM_CMDS:
            dest = Path("/usr/local/bin") / cmd
            try:
                if dest.is_symlink():
                    from .common import java_versions_dir as _jvd
                    target = dest.resolve()
                    if str(_jvd()) in str(target):
                        dest.unlink()
            except Exception:
                pass

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, java_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            java_bin = java_bin_dir(active) / "java"
            if java_bin.exists():
                return active
        java_cmd = shutil.which("java")
        if java_cmd:
            try:
                r = subprocess.run(
                    [java_cmd, "-version"],
                    capture_output=True, text=True, timeout=5,
                )
                # java -version 输出到 stderr
                line = (r.stderr or r.stdout).strip().splitlines()[0]
                # 格式: 'openjdk version "21.0.11" 2026-04-15 LTS'
                if '"' in line:
                    ver = line.split('"')[1]
                    return ver
            except Exception:
                pass
        return None
