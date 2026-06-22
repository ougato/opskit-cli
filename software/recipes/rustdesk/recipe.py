"""RustDesk Server Recipe。"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallStep, Recipe
from software.registry import register
from .constants import RUSTDESK_INSTALLED_VERSION, RUSTDESK_VERSION_LATEST
from .server import detect_version, diagnose_server, install_server, uninstall_server


@register
class RustDeskRecipe(Recipe):
    key: ClassVar[str] = "rustdesk"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "RustDesk 远程桌面服务"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = []

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_install_version_selection: ClassVar[bool] = False

    def detect(self) -> str | None:
        return detect_version()

    def versions(self) -> list[str]:
        return [RUSTDESK_VERSION_LATEST]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("rustdesk.step.download"),
            InstallStep("rustdesk.step.install_binaries"),
            InstallStep("rustdesk.step.configure_service"),
            InstallStep("rustdesk.step.start_service"),
            InstallStep("rustdesk.step.verify"),
            InstallStep("rustdesk.step.print_info"),
        ]

    def install(self, version: str) -> None:
        install_server(version)

    def uninstall(self) -> None:
        uninstall_server()

    def diagnose(self) -> None:
        diagnose_server()

    def system_version(self) -> str | None:
        return RUSTDESK_INSTALLED_VERSION if detect_version() else None
