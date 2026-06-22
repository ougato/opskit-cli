"""Docker Linux 平台驱动"""
from __future__ import annotations

import subprocess

from software.base import InstallError
from .driver import PlatformDriver
from .constants import (
    DOCKER_APT_PACKAGE,
    DOCKER_CE_PACKAGE,
    DOCKER_PACKAGES,
    DOCKER_SERVICE,
    DPKG_QUERY_COMMAND,
    DPKG_STATUS_INSTALLED,
)


class LinuxDriver(PlatformDriver):

    def ensure_deps(self) -> None:
        from core.pkg_runner import get_runner
        try:
            get_runner().update_index()
        except Exception:
            pass

    def pkg_name(self, version: str) -> str:
        from core.platform import get_platform
        pm = get_platform().pkg_manager
        if pm == "apt":
            return DOCKER_APT_PACKAGE
        elif pm in ("yum", "dnf"):
            return DOCKER_CE_PACKAGE
        return DOCKER_CE_PACKAGE

    def detect_package_version(self) -> str | None:
        from core.platform import get_platform
        if get_platform().pkg_manager != "apt":
            return None
        for package in DOCKER_PACKAGES:
            result = subprocess.run(
                [DPKG_QUERY_COMMAND, "-W", "-f=${Status} ${Version}", package],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.startswith(DPKG_STATUS_INSTALLED):
                parts = result.stdout.split()
                return parts[-1] if parts else None
        return None

    def install_pkg(self, pkg: str) -> None:
        from core.pkg_runner import get_runner
        try:
            get_runner().install([pkg])
        except Exception as e:
            raise InstallError(str(e)) from e

    def remove_pkg(self) -> None:
        from core.pkg_runner import get_runner
        try:
            get_runner().remove(DOCKER_PACKAGES)
        except Exception:
            pass

    def enable_service(self) -> None:
        from core.service import enable_now
        try:
            enable_now(DOCKER_SERVICE)
        except Exception:
            pass

    def disable_service(self) -> None:
        from core.service import disable_now
        try:
            disable_now(DOCKER_SERVICE)
        except Exception:
            pass
