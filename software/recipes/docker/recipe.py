"""DockerRecipe 主类：纯调度，零平台 if"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from core.i18n import t
from .constants import DOCKER_VERSIONS_FALLBACK
from .driver import get_driver


@register
class DockerRecipe(Recipe):
    key: ClassVar[str] = "docker"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "Docker 容器引擎"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list[str]] = []

    def detect(self) -> str | None:
        from core.runner import which, run
        if not which("docker"):
            return None
        try:
            result = run(["docker", "--version"], capture=True, check=False)
            if result.returncode == 0:
                line = result.stdout.strip()
                for part in line.split():
                    if part[0].isdigit():
                        return part.rstrip(",")
        except Exception:
            pass
        return None

    def versions(self) -> list[str]:
        from core.constants import TIMEOUT_VERSION_FETCH
        from .constants import DOCKER_GITHUB_API
        try:
            import httpx
            resp = httpx.get(DOCKER_GITHUB_API, timeout=TIMEOUT_VERSION_FETCH)
            if resp.status_code == 200:
                tags = [r["tag_name"].lstrip("v") for r in resp.json()
                        if not r.get("prerelease")]
                if tags:
                    return tags[:8]
        except Exception:
            pass
        return list(DOCKER_VERSIONS_FALLBACK)

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.add_repo"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.enable_service"),
            InstallStep("software.step.verify"),
        ]

    def install(self, version: str) -> None:
        from core.platform import get_platform
        from core.progress import MultiStepProgress

        info = get_platform()
        driver = get_driver()

        descs = ["software.step.check", "software.step.add_repo", "software.step.download",
                 "software.step.install", "software.step.enable_service", "software.step.verify"]
        with MultiStepProgress(descs) as sp:
            sp.step("software.step.check")
            if info.os_type not in self.platforms:
                raise InstallError(t("software.docker_error.platform_not_supported", platform=info.os_type))

            sp.step("software.step.add_repo")
            driver.ensure_deps()

            sp.step("software.step.download")
            pkg = driver.pkg_name(version)

            sp.step("software.step.install")
            driver.install_pkg(pkg)

            sp.step("software.step.enable_service")
            driver.enable_service()

            sp.step("software.step.verify")
            if not self.detect():
                raise InstallError(t("software.docker_error.verify_failed"))
            sp.complete()

    def uninstall(self) -> None:
        from core.platform import get_platform
        from core.progress import MultiStepProgress

        driver = get_driver()

        descs = ["software.step.stop_service", "software.step.remove_files", "software.step.cleanup"]
        with MultiStepProgress(descs) as sp:
            sp.step("software.step.stop_service")
            driver.disable_service()

            sp.step("software.step.remove_files")
            driver.remove_pkg()

            sp.step("software.step.cleanup")
            sp.complete()
