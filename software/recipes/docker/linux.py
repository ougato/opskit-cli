"""Docker Linux 平台驱动"""
from __future__ import annotations

from software.base import InstallError
from .driver import PlatformDriver
from .constants import DOCKER_PACKAGES, DOCKER_SERVICE


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
            return f"docker-ce={version}*"
        elif pm in ("yum", "dnf"):
            return f"docker-ce-{version}"
        return "docker-ce"

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
        from core.privilege import run_as_root
        try:
            run_as_root(["systemctl", "enable", "--now", DOCKER_SERVICE],
                        capture_output=True)
        except Exception:
            pass

    def disable_service(self) -> None:
        from core.privilege import run_as_root
        try:
            run_as_root(["systemctl", "disable", "--now", DOCKER_SERVICE],
                        capture_output=True)
        except Exception:
            pass
