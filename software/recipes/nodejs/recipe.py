"""NodeRecipe 主类：纯调度，零平台 if，所有平台差异通过 PlatformDriver 隔离"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe, UninstallError
from software.registry import register
from core.i18n import t
from .common import (
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    node_bin_dir,
    node_version_dir,
    node_versions_dir,
    version_list,
    download_nodejs_tarball,
)
from .driver import get_driver


@register
class NodeRecipe(Recipe):
    key: ClassVar[str] = "nodejs"
    category: ClassVar[str] = "devtools"
    description: ClassVar[str] = "Node.js 运行时"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    def detect(self) -> str | None:
        return get_driver().detect_active()

    def system_version(self) -> str | None:
        node_cmd = shutil.which("node")
        if node_cmd:
            try:
                import subprocess
                r = subprocess.run([node_cmd, "--version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if line.startswith("v"):
                    return line[1:]
            except Exception:
                pass
        return None

    def installed_versions(self) -> list[str]:
        base = node_versions_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith("node"):
                continue
            ver = name[4:]
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
                raise InstallError(t("software.nodejs_error.platform_not_supported", platform=info.os_type))

            sp.step(t("software.step.download"))
            with tempfile.TemporaryDirectory(prefix="opskit-node-") as tmpdir:
                if sys.platform == "win32":
                    ext = ".zip"
                elif sys.platform == "darwin":
                    ext = ".tar.gz"
                else:
                    ext = ".tar.xz"
                tarball = Path(tmpdir) / f"node-v{version}{ext}"

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
                    download_nodejs_tarball(version, tarball)
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
            snap["node_bin_dir"] = bin_dir
            save_snapshot(snap)

            sp.step(t("software.step.verify"))
            fallback = shutil.which("node") or "node"
            try:
                driver.install_shim(fallback)
            except Exception:
                pass

            try:
                driver.apply_version_link(bin_dir)
            except Exception:
                pass

            if not self.detect():
                raise InstallError(t("software.nodejs_error.verify_failed"))
            sp.complete()

    def upgrade(self, version: str) -> None:
        """升级：直接安装新版本（保留已有版本，不卸载）"""
        self.install(version)

    def switch(self, version: str) -> None:
        installed = self.installed_versions()
        if version not in installed:
            raise InstallError(t("software.nodejs_error.not_installed", version=version))

        # Windows：版本根目录即 bin_dir（node.exe 在根目录）
        # Linux/macOS：bin_dir 是 bin/ 子目录
        if sys.platform == "win32":
            bin_dir = str(node_version_dir(version))
        else:
            bin_dir = str(node_bin_dir(version))

        snap = load_snapshot()
        snap["active_version"] = version
        snap["node_bin_dir"] = bin_dir
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
            d = node_version_dir(ver)
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
                    # 最后一个版本被卸载：全部清理
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
                        new_snap = load_snapshot()
                        new_snap["installed_versions"] = remaining
                        save_snapshot(new_snap)
                else:
                    # 卸载的是非激活版，remaining 非空：只更新快照
                    snap["installed_versions"] = remaining
                    save_snapshot(snap)

            sp.step(t("software.step.cleanup"))
            sp.complete()
