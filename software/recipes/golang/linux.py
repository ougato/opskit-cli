"""Linux 平台驱动：tar.gz 解压、shim sh、shell rc PATH 注入"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from software._shared import shell_path
from .driver import PlatformDriver
from .constants import (
    GOLANG_INSTALL_DIR_LINUX,
    GOLANG_PRIVATE_SUBDIR,
    GOPATH_MARKER_BEGIN,
    GOPATH_MARKER_END,
    SHIM_GO_SH_TEMPLATE,
    PROFILE_D_GO_FILE,
)


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 tar.gz 解压到版本专属目录：~/.opskit/go/go{version}/
        返回 bin 目录路径字符串。
        """
        from .common import go_version_dir, go_bin_dir
        dest = go_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        # 路径统一用 UTF-8 字节串操作：Go 发行包含非 ASCII 文件名的测试
        # 夹具（如 test/fixedbugs/issue27836.dir/Þfoo.go），打包运行时文件系统
        # 编码可能退化为 ascii，str 路径会在 open/stat 时编码失败。
        dest_b = str(dest).encode("utf-8")
        try:
            with tarfile.open(str(tarball), "r:gz") as tf:
                for member in tf.getmembers():
                    if not member.name.startswith("go/"):
                        continue
                    rel = member.name[3:]
                    if not rel:
                        continue
                    target_b = os.path.join(dest_b, rel.encode("utf-8"))
                    if member.isdir():
                        os.makedirs(target_b, exist_ok=True)
                    elif member.isfile():
                        os.makedirs(os.path.dirname(target_b), exist_ok=True)
                        with tf.extractfile(member) as src, open(target_b, "wb") as out:
                            shutil.copyfileobj(src, out)
                        if member.mode & 0o111:
                            os.chmod(target_b, os.stat(target_b).st_mode | 0o111)
        except Exception as e:
            raise InstallError(t("software.golang_error.extract_failed", version=version, error=e)) from e

        bin_dir = go_bin_dir(version)
        if not (bin_dir / "go").exists():
            raise InstallError(t("software.golang_error.bad_structure", version=version, file="bin/go"))
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        shims_path = str(sdir)

        content = SHIM_GO_SH_TEMPLATE.format(fallback=fallback_bin)
        for name in ("go", "gofmt"):
            shim = sdir / name
            shim.write_text(content, encoding="utf-8")
            shim.chmod(0o755)

        # 立即注入当前进程 PATH，让 opskit 内部子进程调用 go 立即生效
        shell_path.prepend_process_path(shims_path)
        shell_path.inject_rc_path(
            shims_path, GOPATH_MARKER_BEGIN, GOPATH_MARKER_END, PROFILE_D_GO_FILE
        )

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()

        for name in ("go", "gofmt"):
            shim = sdir / name
            if shim.exists():
                shim.unlink()
        try:
            sdir.rmdir()
        except Exception:
            pass

        shell_path.remove_rc_path(GOPATH_MARKER_BEGIN, GOPATH_MARKER_END, PROFILE_D_GO_FILE)

    def shim_active(self) -> bool:
        from .common import shim_dir
        return shell_path.process_path_contains(str(shim_dir()))

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """
        优先在 /usr/local/bin 创建/更新 go/gofmt symlink（root 时立即全局生效）。
        非 root 时依赖 shim，同时注入当前进程 PATH 让 opskit 子进程立即可用。
        """
        # root 环境：创建/更新 /usr/local/bin 下的 symlink，立即全局生效
        shell_path.link_into_system_bin(bin_dir, ("go", "gofmt"))
        # 无论是否 root，都把 shim 目录注入当前 Python 进程的 PATH
        # 这样 opskit 内部的子进程调用 go 可以立即路由到正确版本
        from .common import shim_dir as _shim_dir
        shell_path.prepend_process_path(str(_shim_dir()))

    def restore_original(self) -> None:
        """卸载时删除 /usr/local/bin 下 opskit 创建的 symlink"""
        from .common import go_versions_dir as _gvd
        shell_path.unlink_system_bin(("go", "gofmt"), _gvd())

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            from .common import go_bin_dir
            go_bin = go_bin_dir(active) / "go"
            if go_bin.exists():
                return active
        go_cmd = shutil.which("go")
        if go_cmd:
            try:
                r = subprocess.run([go_cmd, "version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if line.startswith("go version go"):
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2][2:]
            except Exception:
                pass
        return None
