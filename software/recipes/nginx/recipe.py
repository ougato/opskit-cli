"""NginxRecipe 主类：纯调度，零平台 if"""
from __future__ import annotations

from typing import Callable, ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from core.i18n import t
from .constants import NGINX_SYSTEM_PACKAGE_VERSION
from .driver import get_driver


@register
class NginxRecipe(Recipe):
    key: ClassVar[str] = "nginx"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "Nginx Web 服务器"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list[str]] = []
    has_upgrade: ClassVar[bool] = False

    def detect(self) -> str | None:
        from core.platform import get_platform
        if get_platform().os_type not in self.platforms:
            return None
        return get_driver().detect()

    def versions(self) -> list[str]:
        return [NGINX_SYSTEM_PACKAGE_VERSION]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.install"),
        ]

    def _do_install(self, on_progress: Callable[[int], None] | None = None) -> None:
        """纯安装逻辑，无进度条。on_progress(pct) 上报 0~100 百分比。"""
        from core.platform import get_platform
        info = get_platform()
        if info.os_type not in self.platforms:
            raise InstallError(t('software.nginx_error.platform_not_supported', platform=info.os_type))
        if on_progress:
            on_progress(20)
        driver = get_driver()
        driver.install_pkg()
        if on_progress:
            on_progress(80)
        driver.enable_service()
        if on_progress:
            on_progress(95)
        if not driver.detect():
            raise InstallError(t('software.nginx_error.verify_failed'))
        if on_progress:
            on_progress(100)

    def install(self, version: str) -> None:
        from core.progress import MultiStepProgress

        descs = [t('software.step.install')]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            self._do_install(on_progress=sp.set_step_pct)
            sp.complete()

    def uninstall(self) -> None:
        from core.progress import MultiStepProgress

        driver = get_driver()

        descs = [
            t("software.step.stop_service"),
            t("software.step.remove_files"),
            t("software.step.cleanup"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            driver.disable_service()

            sp.step(descs[1])
            driver.remove_pkg()

            sp.step(descs[2])
            sp.complete()
