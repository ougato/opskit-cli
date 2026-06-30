"""x-ui Recipe。"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallStep, Recipe
from software.registry import register
from xui.constants import XUI_INSTALLED_VERSION
from xui.server import diagnose_server, install_server, manage_nodes, uninstall_server
from xui.utils import detect_xui_version


@register
class XuiRecipe(Recipe):
    key: ClassVar[str] = "xui"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui / 3x-ui VLESS REALITY TCP 面板"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    has_install_version_selection: ClassVar[bool] = False

    def detect(self) -> str | None:
        return detect_xui_version()

    def versions(self) -> list[str]:
        return [XUI_INSTALLED_VERSION]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("xui.step.check_os"),
            InstallStep("xui.step.install_deps"),
            InstallStep("xui.step.enable_bbr"),
            InstallStep("xui.step.install_xui"),
            InstallStep("xui.step.generate_credentials"),
            InstallStep("xui.step.configure_panel"),
            InstallStep("xui.step.create_vless_tcp"),
            InstallStep("xui.step.start_service"),
            InstallStep("xui.step.verify"),
            InstallStep("xui.step.print_links"),
        ]

    def install(self, version: str) -> None:
        install_server()

    def uninstall(self, version: str | None = None) -> None:
        uninstall_server()

    def diagnose(self) -> None:
        diagnose_server()

    def manage(self) -> None:
        manage_nodes()
