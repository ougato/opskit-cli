"""DockerRecipe 主类：纯调度，零平台 if"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from core.i18n import t
from .constants import DOCKER_SYSTEM_PACKAGE_VERSION
from .driver import get_driver


@register
class DockerRecipe(Recipe):
    key: ClassVar[str] = "docker"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "Docker 容器引擎"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list[str]] = []
    requires_root: ClassVar[bool] = True
    has_upgrade: ClassVar[bool] = False
    has_install_version_selection: ClassVar[bool] = False
    confirm_before_install: ClassVar[bool] = False

    def detect(self) -> str | None:
        from core.platform import get_platform

        info = get_platform()
        if info.os_type not in self.platforms:
            return None
        driver = get_driver()
        package_version = driver.detect_package_version() if hasattr(driver, "detect_package_version") else None
        if package_version:
            return package_version
        if info.pkg_manager == "apt":
            return None
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
        return [DOCKER_SYSTEM_PACKAGE_VERSION]

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

        descs = [
            t("software.step.check"),
            t("software.step.add_repo"),
            t("software.step.download"),
            t("software.step.install"),
            t("software.step.enable_service"),
            t("software.step.verify"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            if info.os_type not in self.platforms:
                raise InstallError(t("software.docker_error.platform_not_supported", platform=info.os_type))

            sp.step(descs[1])
            driver.ensure_deps()

            sp.step(descs[2])
            pkg = driver.pkg_name(version)

            sp.step(descs[3])
            driver.install_pkg(pkg)

            sp.step(descs[4])
            driver.enable_service()

            sp.step(descs[5])
            if not self.detect():
                raise InstallError(t("software.docker_error.verify_failed"))
            sp.complete()

    def uninstall(self, version: str | None = None) -> None:
        from core.platform import get_platform
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
