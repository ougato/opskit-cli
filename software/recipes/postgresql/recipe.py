"""PostgreSQLRecipe 主类：纯调度，零平台 if（对齐 MongoDBRecipe 架构）"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import ClassVar

from core.i18n import t
from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    pgsql_bin_dir,
    pgsql_version_dir,
    pgsql_versions_dir,
    download_pgsql_tarball,
)
from .constants import (
    PGSQL_VERSIONS_FALLBACK,
    PGSQL_VERSIONS_API_URL,
)
from .driver import get_driver


@register
class PostgreSQLRecipe(Recipe):
    key: ClassVar[str] = "postgresql"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "PostgreSQL 关系型数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    def detect(self) -> str | None:
        return get_driver().detect()

    def installed_versions(self) -> list[str]:
        base = pgsql_versions_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith("postgresql"):
                continue
            ver = name[len("postgresql"):]
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
        import sys
        from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
        from core.constants import TIMEOUT_VERSION_FETCH
        from .common import fetch_supported_versions, _theseus_supported

        if sys.platform != "win32":
            _KEY = "postgresql"
            cached = get_cached_versions(_KEY)
            if cached and any(v[0].isdigit() for v in cached if v):
                return cached
            supported = fetch_supported_versions(timeout=TIMEOUT_VERSION_FETCH)
            if supported:
                update_cache(_KEY, supported)
                return supported
            stale = get_cached_versions_stale(_KEY)
            if stale and any(v[0].isdigit() for v in stale if v):
                return stale
            return [v for v in PGSQL_VERSIONS_FALLBACK if _theseus_supported(v)]

        # Windows：从 endoflife.date 获取全量版本（EDB CDN 支持全版本）
        _KEY = "postgresql_win"
        cached = get_cached_versions(_KEY)
        if cached and any(v[0].isdigit() for v in cached if v):
            return cached
        try:
            import httpx
            resp = httpx.get(PGSQL_VERSIONS_API_URL, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                vers = [item.get("latest", "") for item in resp.json()
                        if item.get("latest", "")]
                vers = [v for v in vers if v and v[0].isdigit()]
                if vers:
                    update_cache(_KEY, vers)
                    return vers
        except Exception:
            pass
        stale = get_cached_versions_stale(_KEY)
        if stale and any(v[0].isdigit() for v in stale if v):
            return stale
        return list(PGSQL_VERSIONS_FALLBACK)

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
        from core.platform import get_platform
        from core.progress import MultiStepProgress

        info = get_platform()
        driver = get_driver()

        ext = ".zip" if sys.platform == "win32" else ".tar.gz"
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
                    t("software.postgresql_error.platform_not_supported",
                      os_type=info.os_type,
                      platforms=", ".join(self.platforms))
                )

            sp.step(t("software.step.download"))

            _stop_pct = threading.Event()

            def _ticker():
                pct = 1
                while not _stop_pct.wait(1.0):
                    if pct < 90:
                        pct += 1
                    sp.set_step_pct(pct)

            t_pct = threading.Thread(target=_ticker, daemon=True)
            t_pct.start()
            bin_dir: str = ""
            try:
                if sys.platform == "linux":
                    # Linux 路线：deb 直接解压到版本目录，返回 bin_dir(Path)
                    _bin = download_pgsql_tarball(version, Path("/dev/null"))
                    bin_dir = str(_bin)
                else:
                    with tempfile.TemporaryDirectory(
                        prefix="opskit-pgsql-", ignore_cleanup_errors=True
                    ) as tmpdir:
                        tarball = Path(tmpdir) / f"postgresql-{version}{ext}"
                        download_pgsql_tarball(version, tarball)
                        bin_dir = str(driver.install_tarball(version, tarball))
            finally:
                _stop_pct.set()
                t_pct.join(timeout=2)

            sp.step(t("software.step.install"))
            if sys.platform == "linux":
                # deb 已解压，driver.install_tarball 只做完整性校验
                bin_dir = driver.install_tarball(version, Path(bin_dir))

            snap = load_snapshot()
            if not snap:
                snap = {"installed_versions": [], "active_version": None}

            installed = snap.get("installed_versions", [])
            if version not in installed:
                installed.append(version)
            snap["installed_versions"] = installed
            snap["active_version"] = version
            snap["psql_bin_dir"] = bin_dir
            save_snapshot(snap)

            sp.step(t("software.step.verify"))
            fallback = shutil.which("psql") or "psql"
            try:
                driver.install_shim(fallback)
            except Exception:
                pass
            try:
                driver.apply_version_link(bin_dir)
            except Exception:
                pass

            if not self.detect():
                raise InstallError(t("software.postgresql_error.verify_fail"))
            sp.complete()

    def upgrade(self, version: str) -> None:
        self.install(version)

    def switch(self, version: str) -> None:
        installed = self.installed_versions()
        if version not in installed:
            raise InstallError(
                t("software.postgresql_error.not_installed", version=version)
            )
        bin_dir = str(pgsql_bin_dir(version))
        snap = load_snapshot()
        snap["active_version"] = version
        snap["psql_bin_dir"] = bin_dir
        save_snapshot(snap)
        try:
            get_driver().apply_version_link(bin_dir)
        except Exception:
            pass

    def uninstall(self, version: str | None = None) -> None:
        import shutil as _shutil
        from core.progress import MultiStepProgress

        driver = get_driver()
        snap = load_snapshot()
        active = snap.get("active_version")

        def _remove_version_dir(ver: str) -> None:
            d = pgsql_version_dir(ver)
            if d.exists():
                _shutil.rmtree(str(d), ignore_errors=True)

        descs = [
            t("software.step.remove_files"),
            t("software.step.cleanup"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(t("software.step.remove_files"))
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

            sp.step(t("software.step.cleanup"))
            sp.complete()
