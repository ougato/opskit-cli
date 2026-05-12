"""PythonRecipe 主类：纯调度，零平台 if，所有平台差异通过 PlatformDriver 隔离"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from core.i18n import t
from .common import (
    find_uv_python,
    load_snapshot,
    save_snapshot,
    delete_snapshot,
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
            InstallStep("software.step.install"),
        ]

    def install(self, version: str) -> None:
        from core.progress import MultiStepProgress

        descs = [t("software.step.install")]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            self._do_install(version, on_progress=sp.set_step_pct)
            sp.complete()

    def _do_install(self, version: str, on_progress: Callable[[int], None] | None = None) -> None:
        from core.platform import get_platform

        def _progress(pct: int) -> None:
            if on_progress:
                on_progress(pct)

        info = get_platform()
        driver = get_driver()
        if info.os_type not in self.platforms:
            raise InstallError(t("software.python_error.platform_not_supported", platform=info.os_type))

        _progress(5)
        try:
            uv_bin = driver.ensure_uv()
        except InstallError:
            uv_bin = None

        _progress(15)
        snap = self._load_or_create_snapshot(driver)

        _progress(25)
        major_minor = ".".join(version.split(".")[:2])
        new_bin = self._install_with_uv(version, uv_bin, _progress) if uv_bin else None

        _progress(75)
        if not new_bin:
            new_bin = self._install_with_package_manager(version, major_minor)

        if not new_bin:
            new_bin = self._install_from_source(version)

        if not new_bin:
            raise InstallError(t("software.python_error.install_failed", version=version))

        _progress(90)
        self._activate_install(version, new_bin, snap, driver)
        _progress(100)

    def _load_or_create_snapshot(self, driver) -> dict:
        snap = load_snapshot()
        if snap:
            return snap
        return {
            **driver.snapshot_pre_install(),
            "installed_versions": [],
            "active_version": None,
        }

    def _install_with_uv(
        self,
        version: str,
        uv_bin: str,
        on_progress: Callable[[int], None],
    ) -> str | None:
        import threading as _threading
        from .constants import UV_PYTHON_SUBDIR, UV_PYTHON_TIMEOUT

        env = os.environ.copy()
        env["UV_PYTHON_INSTALL_DIR"] = str(Path.home() / UV_PYTHON_SUBDIR)
        try:
            proc = subprocess.Popen(
                [uv_bin, "python", "install", version],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stop_pct = _threading.Event()

            def time_pct_ticker() -> None:
                pct = 25
                while not stop_pct.wait(1.0):
                    if pct < 85:
                        pct += 1
                    on_progress(pct)

            def drain(pipe) -> None:
                try:
                    pipe.read()
                except Exception:
                    pass

            t_pct = _threading.Thread(target=time_pct_ticker, daemon=True)
            t_out = _threading.Thread(target=drain, args=(proc.stdout,), daemon=True)
            t_err = _threading.Thread(target=drain, args=(proc.stderr,), daemon=True)
            t_pct.start()
            t_out.start()
            t_err.start()

            try:
                proc.wait(timeout=UV_PYTHON_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                stop_pct.set()
                raise InstallError(t("software.python_error.uv_timeout", timeout=UV_PYTHON_TIMEOUT))

            stop_pct.set()
            t_pct.join(timeout=2)
            t_out.join(timeout=2)
            t_err.join(timeout=2)

            if proc.returncode != 0:
                raise InstallError(t("software.python_error.uv_failed", code=proc.returncode))

            return find_uv_python(version)
        except InstallError:
            raise
        except Exception:
            return None

    def _install_with_package_manager(self, version: str, major_minor: str) -> str | None:
        entries = self._version_entries()
        entry = next((e for e in entries if e.display == version), None)
        need_build = entry.need_build if entry else True
        if need_build:
            return None

        from core.pkg_runner import get_runner
        try:
            return get_runner().install_python3(ver=major_minor)
        except Exception:
            return None

    def _install_from_source(self, version: str) -> str | None:
        from .common import build_from_source
        try:
            return build_from_source(version)
        except InstallError:
            raise
        except Exception as e:
            raise InstallError(t("software.python_error.install_failed", version=version)) from e

    def _activate_install(self, version: str, new_bin: str, snap: dict, driver) -> None:
        next_snap = dict(snap)
        installed = list(next_snap.get("installed_versions", []))
        if version not in installed:
            installed.append(version)
        next_snap["installed_versions"] = installed
        next_snap["active_version"] = version
        next_snap["uv_python_path"] = new_bin

        try:
            driver.apply_version_link(new_bin)
        except Exception:
            pass

        fallback = next_snap.get("symlink_path", "") or "/usr/bin/python3"
        try:
            driver.install_shim(fallback)
        except Exception:
            pass

        if not self._python_bin_matches(new_bin, version):
            raise InstallError(t("software.python_error.verify_failed"))
        save_snapshot(next_snap)

    def _python_bin_matches(self, python_bin: str, version: str) -> bool:
        target_minor = ".".join(version.split(".")[:2])
        try:
            result = subprocess.run(
                [python_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            line = result.stdout.strip() or result.stderr.strip()
            if "Python" not in line:
                return False
            detected = line.split()[-1]
            return detected == target_minor or detected.startswith(f"{target_minor}.")
        except Exception:
            return False

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

        descs = [t("software.step.remove_files"), t("software.step.cleanup")]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
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
                switched_version: str | None = None

                if version == active:
                    if remaining:
                        switched = False
                        for fallback_ver in remaining:
                            try:
                                self.switch(fallback_ver)
                                switched = True
                                switched_version = fallback_ver
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
                        if switched_version:
                            snap = load_snapshot()
                            snap["installed_versions"] = remaining
                            snap["active_version"] = switched_version
                            snap["uv_python_path"] = find_uv_python(switched_version) or snap.get("uv_python_path")
                        else:
                            snap["active_version"] = remaining[0] if remaining else None
                    if remaining:
                        save_snapshot(snap)

            sp.step(descs[1])
            sp.complete()
