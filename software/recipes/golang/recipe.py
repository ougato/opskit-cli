"""GoRecipe 主类：纯调度，零平台 if，所有平台差异通过 PlatformDriver 隔离"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe, UninstallError
from software.registry import register
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    go_bin_dir,
    go_version_dir,
    go_versions_dir,
    version_list,
    download_golang_tarball,
)
from .driver import get_driver


@register
class GoRecipe(Recipe):
    key: ClassVar[str] = "golang"
    category: ClassVar[str] = "devtools"
    description: ClassVar[str] = "Go 编程语言"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    def detect(self) -> str | None:
        return get_driver().detect_active()

    def installed_versions(self) -> list[str]:
        base = go_versions_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith("go"):
                continue
            ver = name[2:]
            if ver and ver[0].isdigit():
                versions.append(ver)
        versions.sort(key=lambda v: [int(x) for x in v.split(".") if x.isdigit()], reverse=True)
        return versions

    def _active_version(self) -> str | None:
        return load_snapshot().get("active_version")

    def versions(self) -> list[str]:
        return version_list()

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
        import threading as _threading
        from core.i18n import t
        from core.platform import get_platform
        from core.progress import MultiStepProgress

        info = get_platform()
        driver = get_driver()

        descs = [
            t("software.step.check"),
            t("software.step.download"),
            t("software.step.install"),
            t("software.step.verify"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(t("software.step.check"))
            if info.os_type not in self.platforms:
                raise InstallError(t("software.golang_error.platform_not_supported", platform=info.os_type))

            sp.step(t("software.step.download"))
            with tempfile.TemporaryDirectory(prefix="opskit-go-", ignore_cleanup_errors=True) as tmpdir:
                import sys
                ext = ".zip" if sys.platform == "win32" else ".tar.gz"
                tarball = Path(tmpdir) / f"go{version}{ext}"

                _stop_pct = _threading.Event()

                def _ticker():
                    pct = 1
                    while not _stop_pct.wait(1.0):
                        if pct < 90:
                            pct += 1
                        sp.set_step_pct(pct)

                t_pct = _threading.Thread(target=_ticker, daemon=True)
                t_pct.start()
                try:
                    download_golang_tarball(version, tarball)
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
            snap["go_bin_dir"] = bin_dir
            save_snapshot(snap)

            sp.step(t("software.step.verify"))
            fallback = shutil.which("go") or "go"
            try:
                driver.install_shim(fallback)
            except Exception:
                pass

            try:
                driver.apply_version_link(bin_dir)
            except Exception:
                pass

            if not self.detect():
                raise InstallError(t("software.golang_error.verify_failed"))
            sp.complete()

    def upgrade(self, version: str) -> None:
        """升级：直接安装新版本（保留已有版本，不卸载）"""
        self.install(version)

    def switch(self, version: str) -> None:
        from core.i18n import t as _t
        installed = self.installed_versions()
        if version not in installed:
            raise InstallError(_t("software.golang_error.not_installed", version=version))

        bin_dir = str(go_bin_dir(version))
        snap = load_snapshot()
        snap["active_version"] = version
        snap["go_bin_dir"] = bin_dir
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
            d = go_version_dir(ver)
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
                    # 最后一个版本被卸载：不管是否是激活版，全部清理
                    driver.remove_shim()
                    driver.restore_original()
                    delete_snapshot()
                elif version == active:
                    # 卸载的是激活版，remaining 非空：切换到第一个剩余版本
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
                        # switch() 已写了新快照，重新读取后只更新 installed_versions
                        new_snap = load_snapshot()
                        new_snap["installed_versions"] = remaining
                        save_snapshot(new_snap)
                else:
                    # 卸载的是非激活版，remaining 非空：只更新快照
                    snap["installed_versions"] = remaining
                    save_snapshot(snap)

            sp.step(_t("software.step.cleanup"))
            sp.complete()
