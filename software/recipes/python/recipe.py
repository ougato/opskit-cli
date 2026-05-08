"""PythonRecipe 主类：纯调度，零平台 if，所有平台差异通过 PlatformDriver 隔离"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe, UninstallError
from software.registry import register
from core.i18n import t
from .common import (
    find_uv_python,
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    shim_dir,
    uv_python_dir,
    version_entries,
    VersionEntry,
)
from .driver import get_driver


@register
class PythonRecipe(Recipe):
    key: ClassVar[str] = "python"
    category: ClassVar[str] = "devtools"
    description: ClassVar[str] = "Python 解释器"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    def detect(self) -> str | None:
        return get_driver().detect_active()

    def system_version(self) -> str | None:
        candidates = [f"python3.{minor}" for minor in range(13, 9, -1)] + ["python3", "python"]
        for cmd in candidates:
            if shutil.which(cmd):
                try:
                    r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
                    line = r.stdout.strip() or r.stderr.strip()
                    if "Python" in line:
                        ver = line.split()[-1]
                        if ver.startswith("3."):
                            return ver
                except Exception:
                    pass
        return None

    def installed_versions(self) -> list[str]:
        base = uv_python_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith("cpython-"):
                continue
            parts = name.split("-")
            if len(parts) < 2:
                continue
            ver = parts[1]
            if ver.count(".") >= 2:
                versions.append(ver)
        versions.sort(key=lambda v: [int(x) for x in v.split(".") if x.isdigit()], reverse=True)
        return versions

    def _active_version(self) -> str | None:
        return load_snapshot().get("active_version")

    def _version_entries(self) -> list[VersionEntry]:
        return version_entries()

    def versions(self) -> list[str]:
        return [e.display for e in self._version_entries()]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.install_deps"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.verify"),
        ]

    def install(self, version: str) -> None:
        from core.platform import get_platform
        from core.progress import MultiStepProgress
        from .constants import UV_PYTHON_SUBDIR, UV_PYTHON_TIMEOUT

        info = get_platform()
        driver = get_driver()

        descs = ["check", "install_deps", "download", "install", "verify"]
        with MultiStepProgress(descs) as sp:
            sp.step("check")
            if info.os_type not in self.platforms:
                raise InstallError(t("software.python_error.platform_not_supported", platform=info.os_type))

            sp.step("install_deps")
            try:
                uv_bin = driver.ensure_uv()
            except InstallError:
                uv_bin = None

            sp.step("download")
            pre = driver.snapshot_pre_install()
            snap = load_snapshot()
            if not snap:
                snap = {
                    **pre,
                    "installed_versions": [],
                    "active_version": None,
                }

            sp.step("install")
            new_bin: str | None = None
            major_minor = ".".join(version.split(".")[:2])

            if uv_bin:
                import threading as _threading
                env = os.environ.copy()
                env["UV_PYTHON_INSTALL_DIR"] = str(Path.home() / UV_PYTHON_SUBDIR)
                try:
                    proc = subprocess.Popen(
                        [uv_bin, "python", "install", version],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )

                    _stop_pct = _threading.Event()

                    def _time_pct_ticker():
                        pct = 1
                        while not _stop_pct.wait(1.0):
                            if pct < 90:
                                pct += 1
                            sp.set_step_pct(pct)

                    t_pct = _threading.Thread(target=_time_pct_ticker, daemon=True)
                    t_pct.start()

                    def _drain(pipe):
                        try:
                            pipe.read()
                        except Exception:
                            pass

                    t_out = _threading.Thread(target=_drain, args=(proc.stdout,), daemon=True)
                    t_err = _threading.Thread(target=_drain, args=(proc.stderr,), daemon=True)
                    t_out.start()
                    t_err.start()

                    try:
                        proc.wait(timeout=UV_PYTHON_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        _stop_pct.set()
                        raise InstallError(t("software.python_error.uv_timeout", timeout=UV_PYTHON_TIMEOUT))

                    _stop_pct.set()
                    t_pct.join(timeout=2)
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)

                    if proc.returncode != 0:
                        raise InstallError(t("software.python_error.uv_failed", code=proc.returncode))

                    new_bin = find_uv_python(version)
                except InstallError:
                    raise
                except Exception:
                    new_bin = None

            if not new_bin:
                from core.pkg_runner import get_runner
                runner = get_runner()
                entries = self._version_entries()
                entry = next((e for e in entries if e.display == version), None)
                need_build = entry.need_build if entry else True
                if not need_build:
                    try:
                        new_bin = runner.install_python3(ver=major_minor)
                    except Exception:
                        new_bin = None

            if not new_bin:
                from .common import build_from_source
                try:
                    new_bin = build_from_source(version)
                except Exception as e:
                    raise InstallError(str(e)) from e

            if not new_bin:
                raise InstallError(t("software.python_error.install_failed", version=version))

            installed = snap.get("installed_versions", [])
            if version not in installed:
                installed.append(version)
            snap["installed_versions"] = installed
            snap["active_version"] = version
            snap["uv_python_path"] = new_bin
            save_snapshot(snap)

            sp.step("verify")
            try:
                driver.apply_version_link(new_bin)
            except Exception:
                pass

            fallback = snap.get("symlink_path", "") or "/usr/bin/python3"
            try:
                driver.install_shim(fallback)
            except Exception:
                pass

            if not self.detect():
                raise InstallError(t("software.python_error.verify_failed"))
            sp.complete()

    def switch(self, version: str) -> None:
        new_bin = find_uv_python(version)
        if not new_bin:
            raise InstallError(t("software.python_error.not_installed", version=version))

        snap = load_snapshot()
        snap["active_version"] = version
        snap["uv_python_path"] = new_bin
        save_snapshot(snap)

        try:
            get_driver().apply_version_link(new_bin)
        except Exception:
            pass

    def uninstall(self, version: str | None = None) -> None:
        from core.progress import MultiStepProgress

        snap = load_snapshot()
        active = snap.get("active_version")
        symlink_path = snap.get("symlink_path", "")
        original_target = snap.get("original_target")
        had_local_bin_path = snap.get("had_local_bin_path", False)
        driver = get_driver()

        def _remove_dir(ver: str) -> None:
            base = uv_python_dir()
            if not base.exists():
                return
            for entry in base.iterdir():
                if entry.is_dir() and entry.name.startswith(f"cpython-{ver}"):
                    import shutil as _sh
                    _sh.rmtree(str(entry), ignore_errors=True)

        descs = ["remove", "cleanup"]
        with MultiStepProgress(descs) as sp:
            sp.step("remove")
            installed = self.installed_versions()

            if version is None:
                for v in installed:
                    _remove_dir(v)
                driver.restore_original(symlink_path, original_target, had_local_bin_path)
                driver.remove_shim()
                delete_snapshot()
            else:
                _remove_dir(version)
                remaining = [v for v in installed if v != version]

                if version == active:
                    if remaining:
                        switched = False
                        for fallback_ver in remaining:
                            try:
                                self.switch(fallback_ver)
                                switched = True
                                break
                            except Exception:
                                continue
                        if not switched:
                            driver.restore_original(symlink_path, original_target, had_local_bin_path)
                            delete_snapshot()
                    else:
                        driver.restore_original(symlink_path, original_target, had_local_bin_path)
                        delete_snapshot()

                if snap:
                    snap["installed_versions"] = remaining
                    if version == active:
                        snap["active_version"] = remaining[0] if remaining else None
                    if remaining:
                        save_snapshot(snap)

            sp.step("cleanup")
            sp.complete()
