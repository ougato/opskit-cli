"""MongoDBRecipe 主类：纯调度，零平台 if（对齐 GoRecipe 架构）"""
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
    mongo_bin_dir,
    mongo_version_dir,
    mongo_versions_dir,
    download_mongodb_tarball,
)
from .constants import (
    MONGO_VERSIONS_FALLBACK,
    MONGO_VERSIONS_API_URL,
    MONGO_VERIFY_ERR,
)
from .driver import get_driver


@register
class MongoDBRecipe(Recipe):
    key: ClassVar[str] = "mongodb"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "MongoDB 文档型数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    def detect(self) -> str | None:
        return get_driver().detect_active()

    def installed_versions(self) -> list[str]:
        base = mongo_versions_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith("mongodb"):
                continue
            ver = name[7:]
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
        _KEY = "mongodb"
        cached = get_cached_versions(_KEY)
        if cached and any(v[0].isdigit() for v in cached if v):
            return cached
        try:
            import httpx
            resp = httpx.get(MONGO_VERSIONS_API_URL, timeout=TIMEOUT_VERSION_FETCH)
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
        return list(MONGO_VERSIONS_FALLBACK)

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

        ext = ".zip" if sys.platform == "win32" else ".tgz"
        descs = [
            t("software.step.check"),
            t("software.step.download"),
            t("software.step.install"),
            t("software.step.verify"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(t("software.step.check"))
            if info.os_type not in self.platforms:
                raise InstallError(t("software.mongodb_error.platform_not_supported", platform=info.os_type))

            sp.step(t("software.step.download"))
            with tempfile.TemporaryDirectory(prefix="opskit-mongo-", ignore_cleanup_errors=True) as tmpdir:
                tarball = Path(tmpdir) / f"mongodb-{version}{ext}"

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
                    download_mongodb_tarball(version, tarball)
                finally:
                    _stop_pct.set()
                    t_pct.join(timeout=2)

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
            snap["mongod_bin_dir"] = bin_dir
            save_snapshot(snap)

            sp.step(t("software.step.verify"))
            fallback = shutil.which("mongod") or "mongod"
            try:
                driver.install_shim(fallback)
            except Exception:
                pass

            try:
                driver.apply_version_link(bin_dir)
            except Exception:
                pass

            if not self.detect():
                raise InstallError(t("software.mongodb_error.verify_failed"))
            sp.complete()

    def upgrade(self, version: str) -> None:
        """升级：直接安装新版本（保留已有版本，不卸载）"""
        self.install(version)

    def switch(self, version: str) -> None:
        from core.i18n import t as _t2
        installed = self.installed_versions()
        if version not in installed:
            raise InstallError(_t2("software.mongodb_error.not_installed", version=version))

        bin_dir = str(mongo_bin_dir(version))
        snap = load_snapshot()
        snap["active_version"] = version
        snap["mongod_bin_dir"] = bin_dir
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
            d = mongo_version_dir(ver)
            if d.exists():
                _shutil.rmtree(str(d), ignore_errors=True)

        from core.i18n import t as _t
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
