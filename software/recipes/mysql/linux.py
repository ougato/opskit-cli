"""MySQL Linux 平台驱动：tar.xz 解压、shim sh、shell rc PATH 注入（对齐 mongodb/linux.py）"""
from __future__ import annotations

import shutil

from software.base import InstallError
from core.i18n import t
from software._shared import shell_path
from .driver import PlatformDriver
from .common import extract_tarball, detect_mysql_version
from .constants import (
    MYSQL_PATH_MARKER_BEGIN,
    MYSQL_PATH_MARKER_END,
    SHIM_MYSQL_SH_TEMPLATE,
    PROFILE_D_MYSQL_FILE,
)


def _sofile_exists(pattern: str) -> bool:
    """检查 ldconfig 缓存中是否存在匹配 pattern 的 .so 文件（精确匹配 soname）。"""
    import subprocess
    try:
        r = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5)
        return pattern in r.stdout
    except Exception:
        return False


def _ensure_linux_deps() -> None:
    """
    静默确保 MySQL minimal tarball 所需的系统依赖可用。
    - libaio.so.1     : 所有版本均需要
    - libncurses.so.5 / libtinfo.so.5 : 8.0-8.3 glibc2.17-minimal 的 mysql client 依赖
    用 ldconfig 精确检测 soname 版本，按发行版自动选择包管理器安装。
    全程静默，不中断用户操作，安装失败仅记录日志。
    支持：Debian/Ubuntu/RHEL/CentOS/Rocky/Alma/Fedora/openSUSE/Arch/Alpine。
    """
    import logging
    import subprocess

    logger = logging.getLogger(__name__)

    # (soname精确匹配串, apt包名, dnf/yum包名, zypper包名, pacman包名, apk包名)
    _DEPS = [
        ("libaio.so.1",      "libaio1",     "libaio",       "libaio1",     "libaio",    "libaio"),
        ("libncurses.so.5",  "libncurses5", "ncurses-libs", "libncurses5", "ncurses",   "ncurses-libs"),
        ("libtinfo.so.5",    "libtinfo5",   "ncurses-libs", "libncurses5", "ncurses",   "ncurses-libs"),
    ]

    _PM_ORDER = [
        ("apt-get", lambda pkgs: ["apt-get", "install", "-y"] + pkgs, 1),
        ("dnf",     lambda pkgs: ["dnf",     "install", "-y"] + pkgs, 2),
        ("yum",     lambda pkgs: ["yum",     "install", "-y"] + pkgs, 2),
        ("zypper",  lambda pkgs: ["zypper",  "install", "-y"] + pkgs, 3),
        ("pacman",  lambda pkgs: ["pacman",  "-S", "--noconfirm"] + pkgs, 4),
        ("apk",     lambda pkgs: ["apk",     "add", "--no-cache"] + pkgs, 5),
    ]

    missing_indices = [
        i for i, (soname, *_) in enumerate(_DEPS)
        if not _sofile_exists(soname)
    ]
    if not missing_indices:
        return

    for pm_bin, build_cmd, pm_idx in _PM_ORDER:
        if not shutil.which(pm_bin):
            continue
        pkgs = list(dict.fromkeys(
            _DEPS[i][pm_idx] for i in missing_indices
        ))
        try:
            subprocess.run(build_cmd(pkgs), check=True, capture_output=True)
        except Exception as exc:
            logger.debug("deps install via %s failed: %s", pm_bin, exc)
        break


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball) -> str:
        """
        解压 minimal tarball 到版本专属目录，返回 bin 目录路径字符串。
        minimal tarball 自带 lib/private/ bundled 私有库，安装前静默安装系统依赖。
        """
        from .common import mysql_version_dir, mysql_bin_dir
        _ensure_linux_deps()

        dest = mysql_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        extract_tarball(tarball, dest, version)

        bin_dir = mysql_bin_dir(version)
        if not (bin_dir / "mysql").exists():
            raise InstallError(t("software.mysql_error.linux_bad_structure", version=version))
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

        shell_path.prepend_process_path(shims_path)
        shell_path.inject_rc_path(
            shims_path, MYSQL_PATH_MARKER_BEGIN, MYSQL_PATH_MARKER_END, PROFILE_D_MYSQL_FILE
        )

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

        shell_path.remove_rc_path(MYSQL_PATH_MARKER_BEGIN, MYSQL_PATH_MARKER_END, PROFILE_D_MYSQL_FILE)

    def shim_active(self) -> bool:
        from .common import shim_dir
        return shell_path.process_path_contains(str(shim_dir()))

    # ─── version link ─────────────────────────────────────────────────────────

    def apply_version_link(self, bin_dir: str) -> None:
        """
        root 时在 /usr/local/bin 创建/更新 mysql/mysqld symlink，立即全局生效。
        非 root 时依赖 shim，注入当前进程 PATH。
        """
        shell_path.link_into_system_bin(bin_dir, ("mysql", "mysqld", "mysqladmin", "mysqldump"))
        from .common import shim_dir as _shim_dir
        shell_path.prepend_process_path(str(_shim_dir()))

    def restore_original(self) -> None:
        """卸载时删除 /usr/local/bin 下 opskit 创建的 symlink"""
        from .common import mysql_versions_dir as _mvd
        shell_path.unlink_system_bin(("mysql", "mysqld", "mysqladmin", "mysqldump"), _mvd())

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        return detect_mysql_version("mysql")
