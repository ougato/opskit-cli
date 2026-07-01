"""Tailscale Recipe。"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallStep, Recipe
from software.registry import register
from tailscale.constants import TAILSCALE_INSTALLED_VERSION, TAILSCALE_VERSION_LATEST
from tailscale.server import detect_tailscale_version, diagnose_client, install_client, manage_client, uninstall_client


@register
class TailscaleRecipe(Recipe):
    key: ClassVar[str] = "tailscale"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "Tailscale WireGuard 组网"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = []
    requires_root: ClassVar[bool] = True

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    has_install_version_selection: ClassVar[bool] = False

    def detect(self) -> str | None:
        return detect_tailscale_version()

    def versions(self) -> list[str]:
        return [TAILSCALE_VERSION_LATEST]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("tailscale.step.check_os"),
            InstallStep("tailscale.step.install"),
            InstallStep("tailscale.step.start"),
            InstallStep("tailscale.step.exit_node"),
            InstallStep("tailscale.step.login"),
        ]

    def install(self, version: str) -> None:
        install_client()

    def uninstall(self, version: str | None = None) -> None:
        uninstall_client()

    def diagnose(self) -> None:
        diagnose_client()

    def manage(self) -> None:
        manage_client()
