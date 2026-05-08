"""MySQLRecipe 主类：纯调度，零平台 if（对齐 MongoDBRecipe 架构）"""
from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    mysql_bin_dir,
    mysql_version_dir,
    mysql_versions_dir,
    download_mysql_tarball,
)
from .constants import (
    MYSQL_VERSIONS_FALLBACK,
    MYSQL_VERSIONS_API_URL,
    MYSQL_NO_5X_CODENAMES,
)
from .driver import get_driver


def _dedup_versions_by_series(versions: list[str]) -> list[str]:
    """
    精简版本列表，避免已下架版本出现：
    - Innovation 系列（major >= 9）：只保留版本号最高的一个（Oracle 只保留最新 Innovation）
    - LTS 系列（8.x）：每个 major.minor 只保留最新版
    - 5.x：每个 major.minor 只保留最新版
    """
    def _ver_tuple(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0,)

    innovation: list[str] = []
    lts: list[str] = []
    old: list[str] = []

    for v in versions:
        try:
            major = int(v.split(".")[0])
        except (ValueError, IndexError):
            continue
        if major >= 9:
            innovation.append(v)
        elif major >= 8:
            lts.append(v)
        else:
            old.append(v)

    result: list[str] = []

    # Innovation：只取版本号最大的一个
    if innovation:
        result.append(max(innovation, key=_ver_tuple))

    # LTS：每个 major.minor 取最新
    seen_lts: set[str] = set()
    for v in sorted(lts, key=_ver_tuple, reverse=True):
        series = ".".join(v.split(".")[:2])
        if series not in seen_lts:
            seen_lts.add(series)
            result.append(v)

    # 5.x 等旧版：每个 major.minor 取最新
    seen_old: set[str] = set()
    for v in sorted(old, key=_ver_tuple, reverse=True):
        series = ".".join(v.split(".")[:2])
        if series not in seen_old:
            seen_old.add(series)
            result.append(v)

    return result


def _filter_versions_by_platform(versions: list[str]) -> list[str]:
    """
    按平台静态过滤版本列表，无网络探针：
    - Windows：过滤掉 5.5.x 及以下（官方无 Windows 包）
    - macOS：过滤掉全部 5.x（官方无 macOS 包）
    - Linux 新系统（bookworm/jammy+）：过滤掉全部 5.x
    - Linux 旧系统：原样返回
    """
    import sys
    from .common import get_distro_codename
    if sys.platform == "win32":
        return [v for v in versions if not v.startswith(("5.5.", "5.4.", "5.3.", "5.2.", "5.1.", "5.0."))]
    if sys.platform == "darwin":
        return [v for v in versions if not v.startswith(("5.", "4.", "3."))]
    if get_distro_codename() in MYSQL_NO_5X_CODENAMES:
        return [v for v in versions if not v.startswith(("5.", "4.", "3."))]
    return versions


@register
class MySQLRecipe(Recipe):
    key: ClassVar[str] = "mysql"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "MySQL 关系型数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    def detect(self) -> str | None:
        return get_driver().detect_active()

    def installed_versions(self) -> list[str]:
        base = mysql_versions_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith("mysql"):
                continue
            ver = name[5:]
            if ver and ver[0].isdigit():
                versions.append(ver)
        versions.sort(
            key=lambda v: [int(x) for x in v.split(".") if x.isdigit()],
            reverse=True,
        )
        return versions

    def _active_version(self) -> str | None:
        return load_snapshot().get("active_version")

    def versions(self) -> list[str]:
        from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
        from core.constants import TIMEOUT_VERSION_FETCH
        _KEY = "mysql"
        cached = get_cached_versions(_KEY)
        if cached and any(v[0].isdigit() for v in cached if v):
            return _filter_versions_by_platform(_dedup_versions_by_series(cached))
        try:
            import httpx
            resp = httpx.get(MYSQL_VERSIONS_API_URL, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                vers = [item.get("latest", "") for item in resp.json()
                        if item.get("latest", "")]
                vers = [v for v in vers if v and v[0].isdigit()]
                if vers:
                    vers = _dedup_versions_by_series(vers)
                    vers = _filter_versions_by_platform(vers)
                if vers:
                    update_cache(_KEY, vers)
                    return vers
        except Exception:
            pass
        stale = get_cached_versions_stale(_KEY)
        if stale and any(v[0].isdigit() for v in stale if v):
            return _filter_versions_by_platform(_dedup_versions_by_series(stale))
        return list(MYSQL_VERSIONS_FALLBACK)

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.verify"),
        ]

    def install(self, version: str) -> None:
        import sys
        from core.i18n import t
        from core.platform import get_platform
        from core.progress import MultiStepProgress

        info = get_platform()
        driver = get_driver()

        ext = ".zip" if sys.platform == "win32" else ".tar.xz" if sys.platform != "darwin" else ".tar.gz"
        descs = [
            t("software.step.check"),
            t("software.step.download"),
            t("software.step.install"),
            t("software.step.verify"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(t("software.step.check"))
            if info.os_type not in self.platforms:
                raise InstallError(
                    t("software.mysql_error.platform_not_supported", platform=info.os_type)
                )

            sp.step(t("software.step.download"))
            with tempfile.TemporaryDirectory(prefix="opskit-mysql-", ignore_cleanup_errors=True) as tmpdir:
                tarball_dest = Path(tmpdir) / f"mysql-{version}{ext}"

                _stop_pct = threading.Event()

                def _ticker():
                    pct = 1
                    while not _stop_pct.wait(1.0):
                        if pct < 90:
                            pct += 1
                        sp.set_step_pct(pct)

                t_pct = threading.Thread(target=_ticker, daemon=True)
                t_pct.start()
                try:
                    dl_result = download_mysql_tarball(version, tarball_dest)
                finally:
                    _stop_pct.set()
                    t_pct.join(timeout=2)

                # dl_result 可能是 Path（tarball）或 list[Path]（APT deb 包列表）
                tarball = dl_result  # type: ignore[assignment]

                sp.step(t("software.step.install"))
                pre = driver.snapshot_pre_install()
                snap = load_snapshot()
                if not snap:
                    snap = {
                        **pre,
                        "installed_versions": [],
                        "active_version": None,
                    }

                bin_dir = driver.install_tarball(version, tarball)

            installed = snap.get("installed_versions", [])
            if version not in installed:
                installed.append(version)
            snap["installed_versions"] = installed
            snap["active_version"] = version
            snap["mysql_bin_dir"] = bin_dir
            save_snapshot(snap)

            sp.step(t("software.step.verify"))
            fallback = shutil.which("mysql") or "mysql"
            try:
                driver.install_shim(fallback)
            except Exception:
                pass

            try:
                driver.apply_version_link(bin_dir)
            except Exception:
                pass

            if not self.detect():
                raise InstallError(t("software.mysql_error.verify_failed"))
            sp.complete()

    def upgrade(self, version: str) -> None:
        """升级：直接安装新版本（保留已有版本，不卸载）"""
        self.install(version)

    def switch(self, version: str) -> None:
        installed = self.installed_versions()
        if version not in installed:
            from core.i18n import t
            raise InstallError(t("software.mysql_error.not_installed", version=version))

        bin_dir = str(mysql_bin_dir(version))
        snap = load_snapshot()
        snap["active_version"] = version
        snap["mysql_bin_dir"] = bin_dir
        save_snapshot(snap)

        try:
            get_driver().apply_version_link(bin_dir)
        except Exception:
            pass

    def uninstall(self, version: str | None = None) -> None:
        import shutil as _shutil
        from core.i18n import t as _t
        from core.progress import MultiStepProgress

        driver = get_driver()
        snap = load_snapshot()
        active = snap.get("active_version")

        def _remove_version_dir(ver: str) -> None:
            d = mysql_version_dir(ver)
            if d.exists():
                _shutil.rmtree(str(d), ignore_errors=True)

        descs = [
            _t("software.step.remove_files"),
            _t("software.step.cleanup"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(_t("software.step.remove_files"))
            installed = self.installed_versions()

            if version is None:
                for v in installed:
                    _remove_version_dir(v)
                driver.remove_shim()
                driver.restore_original()
                delete_snapshot()
            else:
                _remove_version_dir(version)
                remaining = [v for v in installed if v != version]

                if not remaining:
                    driver.remove_shim()
                    driver.restore_original()
                    delete_snapshot()
                elif version == active:
                    switched = False
                    for fallback_ver in remaining:
                        try:
                            self.switch(fallback_ver)
                            switched = True
                            break
                        except Exception:
                            continue
                    if not switched:
                        driver.remove_shim()
                        driver.restore_original()
                        delete_snapshot()
                    else:
                        new_snap = load_snapshot()
                        new_snap["installed_versions"] = remaining
                        save_snapshot(new_snap)
                else:
                    snap["installed_versions"] = remaining
                    save_snapshot(snap)

            sp.step(_t("software.step.cleanup"))
            sp.complete()
