"""NginxRecipe 主类：纯调度，零平台 if"""
from __future__ import annotations

from typing import Callable, ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from core.i18n import t
from .constants import NGINX_VERSIONS_FALLBACK
from .driver import get_driver


@register
class NginxRecipe(Recipe):
    key: ClassVar[str] = "nginx"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "Nginx Web 服务器"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list[str]] = []

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

    def versions(self) -> list[str]:
        from core.constants import TIMEOUT_VERSION_FETCH
        from .constants import NGINX_GITHUB_API
        try:
            import httpx
            resp = httpx.get(NGINX_GITHUB_API, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                tags = [t["name"].lstrip("release-") for t in resp.json()]
                stable = [v for v in tags if not any(c in v for c in ("alpha", "beta", "rc"))]
                if stable:
                    return stable[:6]
        except Exception:
            pass
        return list(NGINX_VERSIONS_FALLBACK)

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.enable_service"),
            InstallStep("software.step.verify"),
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
        if not self.detect():
            raise InstallError(t('software.nginx_error.verify_failed'))
        if on_progress:
            on_progress(100)

    def install(self, version: str) -> None:
        from core.progress import MultiStepProgress

        descs = [
            t('software.step.check'),
            t('software.step.download'),
            t('software.step.install'),
            t('software.step.enable_service'),
            t('software.step.verify'),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            sp.step(descs[1])
            sp.step(descs[2])
            self._do_install(on_progress=sp.set_step_pct)
            sp.complete()

    def uninstall(self) -> None:
        from core.progress import MultiStepProgress

        driver = get_driver()

        descs = ["stop", "remove", "cleanup"]
        with MultiStepProgress(descs) as sp:
            sp.step("stop")
            driver.disable_service()

            sp.step("remove")
            driver.remove_pkg()

            sp.step("cleanup")
            sp.complete()
