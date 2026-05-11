"""Nginx Linux 平台驱动"""
from __future__ import annotations

from software.base import InstallError
from .driver import PlatformDriver
from .constants import NGINX_PACKAGE, NGINX_EXTRA_PACKAGES, NGINX_SERVICE


class LinuxDriver(PlatformDriver):

    def install_pkg(self) -> None:
        from core.pkg_runner import get_runner
        try:
            runner = get_runner()
            runner.update_index()
            runner.install([NGINX_PACKAGE] + NGINX_EXTRA_PACKAGES)
        except Exception as e:
            raise InstallError(str(e)) from e

    def remove_pkg(self) -> None:
        from core.pkg_runner import get_runner
        try:
            get_runner().remove([NGINX_PACKAGE])
        except Exception:
            pass

    def enable_service(self) -> None:
        from core.privilege import run_as_root
        try:
            run_as_root(["systemctl", "enable", "--now", NGINX_SERVICE],
                        capture_output=True)
        except Exception:
            pass

    def disable_service(self) -> None:
        from core.privilege import run_as_root
        try:
            run_as_root(["systemctl", "disable", "--now", NGINX_SERVICE],
                        capture_output=True)
        except Exception:
            pass
