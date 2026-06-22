"""x-ui Recipe。"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallStep, Recipe
from software.registry import register
from xui.constants import XUI_INSTALLED_VERSION, XUI_VERSION_LATEST
from xui.server import diagnose_server, install_server, manage_nodes, uninstall_server
from xui.utils import detect_xui_version


@register
class XuiRecipe(Recipe):
    key: ClassVar[str] = "xui"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui / 3x-ui 面板"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = []

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = False
    has_submenu: ClassVar[bool] = True
    has_wizard: ClassVar[bool] = False
    has_install_version_selection: ClassVar[bool] = False

    def detect(self) -> str | None:
        return detect_xui_version()

    def versions(self) -> list[str]:
        return [XUI_VERSION_LATEST]

    def install(self, version: str) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def submenu_items(self) -> list[dict]:
        return [
            {"key": "xui_server", "label_key": "software.xui_server"},
        ]


@register
class XuiServerRecipe(Recipe):
    key: ClassVar[str] = "xui_server"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui VLESS REALITY XHTTP / Trojan 服务端"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}, "tailscale"]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    has_install_version_selection: ClassVar[bool] = False
    hidden: ClassVar[bool] = True

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
            InstallStep("xui.step.install_xui"),
            InstallStep("xui.step.generate_credentials"),
            InstallStep("xui.step.configure_panel"),
            InstallStep("xui.step.create_vless_xhttp"),
            InstallStep("xui.step.create_trojan"),
            InstallStep("xui.step.start_service"),
            InstallStep("xui.step.verify"),
            InstallStep("xui.step.print_links"),
        ]

    def install(self, version: str) -> None:
        install_server()

    def uninstall(self) -> None:
        uninstall_server()

    def diagnose(self) -> None:
        diagnose_server()

    def manage(self) -> None:
        manage_nodes()
