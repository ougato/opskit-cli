"""PythonRecipe 主类：纯调度，零平台 if，所有平台差异通过 PlatformDriver 隔离"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, ClassVar

from software.base import InstallError, InstallStep, Recipe
from software._shared.mutation import versioned_mutation
from software.registry import register
from core.i18n import t
from .common import (
    find_uv_python,
    is_stable_pyver,
    load_snapshot,
    save_snapshot,
    delete_snapshot,
    uv_python_dir,
    uv_installation_directories,
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
        from .version_probe import interpreter_version
        candidates = [f"python3.{minor}" for minor in range(13, 9, -1)] + ["python3", "python"]
        for command in candidates:
            executable = shutil.which(command)
            if executable:
                version = interpreter_version(executable)
                if version and version.startswith("3."):
                    return version
        return None

    def installed_versions(self) -> list[str]:
        base = uv_python_dir()
        versions: list[str] = []
        for entry in uv_installation_directories(base):
            name = entry.name
            if not name.startswith("cpython-"):
                continue
            parts = name.split("-")
            if len(parts) < 2:
                continue
            ver = parts[1]
            if ver.count(".") >= 2 and is_stable_pyver(ver):
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

    @versioned_mutation
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
        from core.process import capture
        from .constants import UV_PYTHON_SUBDIR, UV_PYTHON_TIMEOUT

        env = os.environ.copy()
        env["UV_PYTHON_INSTALL_DIR"] = str(Path.home() / UV_PYTHON_SUBDIR)
        on_progress(25)
        try:
            result = capture(
                [uv_bin, "python", "install", version], env=env,
                timeout=UV_PYTHON_TIMEOUT, max_bytes=1024 * 1024,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as error:
            raise InstallError(t("software.python_error.uv_timeout", timeout=UV_PYTHON_TIMEOUT)) from error
        except RuntimeError as error:
            raise InstallError("uv Python installation exceeded its output or process resource limit") from error
        except OSError:
            return None
        if result.returncode != 0:
            # Download output may contain proxy credentials or private URLs.
            # Keep the exit code without echoing raw child output to logs/UI.
            raise InstallError(t("software.python_error.uv_failed", code=result.returncode, detail=""))
        on_progress(75)
        return find_uv_python(version)

    def _install_with_package_manager(self, version: str, major_minor: str) -> str | None:
        entries = self._version_entries()
        entry = next((e for e in entries if e.display == version), None)
        need_build = entry.need_build if entry else True
        if need_build:
            return None

        from core.pkg_runner import get_runner
        try:
            installed = get_runner().install_python3(ver=major_minor)
            return installed if installed and self._python_bin_matches(installed, version) else None
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
        # Refuse an incorrect interpreter before modifying links, shims or PATH.
        if not self._python_bin_matches(new_bin, version):
            raise InstallError(t("software.python_error.verify_failed"))
        next_snap = dict(snap)
        installed = list(next_snap.get("installed_versions", []))
        if version not in installed:
            installed.append(version)
        next_snap["installed_versions"] = installed
        next_snap["active_version"] = version
        next_snap["uv_python_path"] = new_bin

        fallback = next_snap.get("symlink_path", "") or "/usr/bin/python3"
        def apply():
            driver.apply_version_link(new_bin)
            driver.install_shim(fallback)

        def verify():
            if not self._python_bin_matches(new_bin, version):
                raise InstallError(t("software.python_error.verify_failed"))

        def restore():
            previous_bin = snap.get("uv_python_path")
            if previous_bin:
                driver.apply_version_link(previous_bin)
            else:
                driver.restore_original(snap.get("symlink_path", ""), snap.get("original_target"),
                                        snap.get("had_local_bin_path", False))
                driver.remove_shim()

        from software._shared.activation import activate_version
        activate_version(snap, next_snap, save=save_snapshot, apply=apply, restore=restore, verify=verify)

    def _python_bin_matches(self, python_bin: str, version: str) -> bool:
        import re
        from .version_probe import interpreter_version
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            return False
        try:
            return interpreter_version(python_bin) == version
        except Exception:
            return False

    @versioned_mutation
    def upgrade(self, version: str) -> None:
        """Python 多版本共存：升级即安装新版本并激活，不卸载已装的其它版本。

        覆盖基类「卸载 + 安装」默认实现，避免升级时清掉用户并存的旧版本。
        """
        self.install(version)

    @versioned_mutation
    def switch(self, version: str) -> None:
        new_bin = find_uv_python(version)
        if not new_bin:
            raise InstallError(t("software.python_error.not_installed", version=version))

        snap = load_snapshot()
        previous_bin = snap.get("uv_python_path")
        driver = get_driver()
        from software._shared.activation import activate_version, verify_command_version
        activate_version(snap, {"active_version": version, "uv_python_path": new_bin},
                         save=save_snapshot,
                         apply=lambda: driver.apply_version_link(new_bin),
                         restore=lambda: driver.apply_version_link(previous_bin) if previous_bin else driver.restore_original(
                             snap.get("symlink_path", ""), snap.get("original_target"), snap.get("had_local_bin_path", False)),
                         verify=lambda: verify_command_version("python" if sys.platform == "win32" else "python3", version))

    @versioned_mutation
    def uninstall(self, version: str | None = None) -> None:
        from core.progress import MultiStepProgress
        from software._shared.activation import ActivationRollbackError

        if version is not None and (not is_stable_pyver(version) or len(version.split(".")) != 3):
            raise InstallError("Python uninstall requires an exact stable version")

        snap = load_snapshot()
        active = snap.get("active_version")
        symlink_path = snap.get("symlink_path", "")
        original_target = snap.get("original_target")
        had_local_bin_path = snap.get("had_local_bin_path", False)
        driver = get_driver()

        def _remove_dir(ver: str) -> None:
            base = uv_python_dir()
            for entry in uv_installation_directories(base):
                if entry.name.startswith(f"cpython-{ver}-"):
                    try:
                        shutil.rmtree(entry)
                    except OSError as error:
                        raise InstallError("Python installation directory removal failed") from error

        descs = [t("software.step.remove_files"), t("software.step.cleanup")]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            installed = self.installed_versions()

            if version is None:
                # Validate every managed directory before changing routing.
                uv_installation_directories(uv_python_dir())
                driver.restore_original(symlink_path, original_target, had_local_bin_path)
                driver.remove_shim()
                for v in installed:
                    _remove_dir(v)
                delete_snapshot()
            else:
                remaining = [v for v in installed if v != version]
                switched_version: str | None = None

                if version == active and remaining:
                    for fallback_ver in remaining:
                        try:
                            self.switch(fallback_ver)
                            switched_version = fallback_ver
                            break
                        except ActivationRollbackError:
                            # An uncertain route/state must not trigger another
                            # switch attempt or any destructive directory work.
                            raise
                        except Exception:
                            continue
                    if switched_version is None:
                        raise InstallError("Python uninstall could not activate a retained version; installation preserved")

                if version == active and not remaining:
                    uv_installation_directories(uv_python_dir())
                    driver.restore_original(symlink_path, original_target, had_local_bin_path)
                    driver.remove_shim()

                _remove_dir(version)
                if version == active and not remaining:
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
