"""Nginx Linux 平台驱动"""
from __future__ import annotations

from software.base import InstallError, UninstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import NGINX_PACKAGE, NGINX_EXTRA_PACKAGES, NGINX_SERVICE


class LinuxDriver(PlatformDriver):

    def detect(self) -> str | None:
        from core.runner import which, run
        if not which("nginx"):
            return None
        try:
            result = run(["nginx", "-v"], capture=True, check=False)
            output = result.stderr.strip() if result.stderr else result.stdout.strip()
            if "nginx/" in output:
                return output.split("nginx/")[-1].split()[0]
        except Exception:
            pass
        return None

    def install_pkg(self) -> None:
        from core.pkg_runner import get_runner
        try:
            runner = get_runner()
            runner.update_index()
            runner.install(self._packages_for_runner(runner.name))
        except Exception as e:
            raise InstallError(t("software.nginx_error.install_failed", detail=str(e))) from e

    def remove_pkg(self) -> None:
        from core.pkg_runner import get_runner
        try:
            get_runner().remove([NGINX_PACKAGE])
        except Exception as e:
            raise UninstallError(t("software.nginx_error.remove_failed", detail=str(e))) from e

    def enable_service(self) -> None:
        from core.privilege import run_as_root
        try:
            run_as_root(["systemctl", "enable", "--now", NGINX_SERVICE],
                        capture_output=True, text=True, check=True)
        except Exception as e:
            raise InstallError(t("software.nginx_error.service_enable_failed", detail=str(e))) from e

    def disable_service(self) -> None:
        from core.privilege import run_as_root
        try:
            run_as_root(["systemctl", "disable", "--now", NGINX_SERVICE],
                        capture_output=True)
        except Exception:
            pass

    def _packages_for_runner(self, runner_name: str) -> list[str]:
        if runner_name == "apt":
            return [NGINX_PACKAGE] + NGINX_EXTRA_PACKAGES
        return [NGINX_PACKAGE]
