"""MySQL Linux 平台驱动：tar.xz 解压、shim sh、shell rc PATH 注入（对齐 mongodb/linux.py）"""
from __future__ import annotations

import shutil
import subprocess

from core.deadline import Deadline
from core.process import capture

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


DEPENDENCY_TIMEOUT = 180


def _installed_sonames(budget, machine) -> set[str]:
    """Read one bounded linker-cache snapshot, matching complete sonames."""
    from pathlib import Path
    executable = shutil.which('ldconfig')
    if executable is None:
        executable = next((str(path) for path in (Path('/sbin/ldconfig'), Path('/usr/sbin/ldconfig'))
                           if path.is_file()), None)
    if executable is None:
        raise InstallError('MySQL requires an available ldconfig dependency check')
    result = budget.call(capture, [executable, '-p'], timeout=budget.remaining(5),
                         max_bytes=1024**2, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise InstallError('MySQL linker-cache dependency check failed')
    architecture = 'x86-64' if machine == 62 else 'AArch64'
    return {line.split()[0] for line in result.stdout.splitlines()
            if '=>' in line and architecture in line.partition('=>')[0] and line.split()}


def _ensure_linux_deps(root) -> None:
    """
    从已提取的ELF元数据读取四种命令及私有库的实际依赖，不执行下载程序。
    使用同架构ldconfig缓存精确检测soname；只安装已声明映射的缺失库。
    使用一次缓存快照；安装和复核共享期限，失败阻止发布新版本目录。
    包名映射仅是候选；包管理器成功但所需soname仍缺失也必须失败。
    """
    # (soname精确匹配串, apt包名, dnf/yum包名, zypper包名, pacman包名, apk包名)
    _DEPS = [
        ("libaio.so.1",      "libaio1",     "libaio",       "libaio1",     "libaio",    "libaio"),
        ("libnuma.so.1",     "libnuma1",    "numactl-libs", "libnuma1",    "numactl",   "numactl"),
        ("libncurses.so.5",  "libncurses5", "ncurses-libs", "libncurses5", "ncurses",   "ncurses-libs"),
        ("libtinfo.so.5",    "libtinfo5",   "ncurses-libs", "libncurses5", "ncurses",   "ncurses-libs"),
        ("libncurses.so.6",  "libncurses6", "ncurses-libs", "libncurses6", "ncurses",   "ncurses-libs"),
        ("libtinfo.so.6",    "libtinfo6",   "ncurses-libs", "libncurses6", "ncurses",   "ncurses-libs"),
    ]

    _PM_ORDER = [
        ("apt-get", lambda pkgs: ["apt-get", "install", "-y"] + pkgs, 1),
        ("dnf",     lambda pkgs: ["dnf",     "install", "-y"] + pkgs, 2),
        ("yum",     lambda pkgs: ["yum",     "install", "-y"] + pkgs, 2),
        ("zypper",  lambda pkgs: ["zypper",  "install", "-y"] + pkgs, 3),
        ("pacman",  lambda pkgs: ["pacman",  "-S", "--noconfirm"] + pkgs, 4),
        ("apk",     lambda pkgs: ["apk",     "add", "--no-cache"] + pkgs, 5),
    ]

    budget = Deadline(DEPENDENCY_TIMEOUT, 'MySQL system dependencies')
    try:
        import platform
        from .elf_dependencies import runtime_requirements
        machine = {'x86_64': 62, 'amd64': 62, 'aarch64': 183, 'arm64': 183}.get(platform.machine().lower())
        if machine is None:
            raise InstallError('MySQL runtime architecture is unsupported')
        required = runtime_requirements(root, budget, machine)
        if not required:
            return
        installed = _installed_sonames(budget, machine)
        missing = required - installed
        if not missing:
            return
        unmapped = missing - {entry[0] for entry in _DEPS}
        if unmapped:
            raise InstallError('MySQL system libraries require manual provisioning: ' + ', '.join(sorted(unmapped)))
        missing_indices = [i for i, (soname, *_) in enumerate(_DEPS) if soname in missing]
        if not missing_indices:
            return
        for pm_bin, build_cmd, pm_idx in _PM_ORDER:
            executable = shutil.which(pm_bin)
            if executable is None:
                continue
            pkgs = list(dict.fromkeys(_DEPS[i][pm_idx] for i in missing_indices))
            arguments = build_cmd(pkgs)
            arguments[0] = executable
            result = budget.call(capture, arguments, timeout=budget.remaining(),
                                 max_bytes=256 * 1024, stdin=subprocess.DEVNULL)
            if result.returncode:
                raise InstallError('MySQL system dependency installation failed via ' + pm_bin)
            installed = _installed_sonames(budget, machine)
            missing = sorted(required - installed)
            if missing:
                raise InstallError('MySQL system libraries still missing: ' + ', '.join(missing))
            return
        raise InstallError('MySQL system libraries are missing and no supported package manager is available')
    except InstallError:
        raise
    except (OSError, RuntimeError, ValueError, TimeoutError, subprocess.SubprocessError):
        raise InstallError('MySQL system dependency operation failed or exceeded its deadline') from None


class LinuxDriver(PlatformDriver):

    # ─── tarball 安装 ─────────────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball) -> str:
        """
        解压 minimal tarball 到版本专属目录，返回 bin 目录路径字符串。
        minimal tarball 自带 lib/private/ bundled 私有库，安装前静默安装系统依赖。
        """
        from .common import mysql_version_dir, mysql_bin_dir
        from software._shared.archive_install import staged_directory
        dest = mysql_version_dir(version)
        try:
            with staged_directory(dest, prefix=".opskit-mysql-") as staging:
                extract_tarball(tarball, staging, version)
                if not (staging / "bin/mysql").is_file():
                    raise InstallError(t("software.mysql_error.linux_bad_structure", version=version))
                _ensure_linux_deps(staging)
        except FileExistsError as error:
            raise InstallError("MySQL version directory already exists; refusing to overwrite") from error
        except InstallError:
            raise
        except Exception as error:
            raise InstallError(t("software.mysql_error.extract_failed", version=version, error=error)) from error
        bin_dir = mysql_bin_dir(version)
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
        from .common import mysql_versions_dir
        shell_path.link_into_system_bin(bin_dir, ("mysql", "mysqld", "mysqladmin", "mysqldump"), managed_root=mysql_versions_dir())
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
