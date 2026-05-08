"""WireGuard 父级配方、服务端配方、客户端配方"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from .driver import get_driver


@register
class WireGuardRecipe(Recipe):
    key: ClassVar[str] = "wireguard"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "WireGuard VPN 隧道"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list[str]] = []

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = False
    has_submenu: ClassVar[bool] = True
    has_wizard: ClassVar[bool] = False

    def detect(self) -> str | None:
        return None

    def versions(self) -> list[str]:
        return ["latest"]

    def install(self, version: str) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def submenu_items(self) -> list[dict]:
        return [
            {"key": "wg_server", "label_key": "software.wg_server"},
            {"key": "wg_client", "label_key": "software.wg_client"},
        ]


@register
class WgServerRecipe(Recipe):
    key: ClassVar[str] = "wg_server"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "WireGuard 公网服务端（over Xray REALITY）"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}, "nginx"]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    hidden: ClassVar[bool] = True

    def detect(self) -> str | None:
        from core.sysconfig import _load as _sc_load
        entry = _sc_load().get("wg_server", {})
        if entry.get("status") != "installed":
            return None
        from pathlib import Path
        from wireguard.constants import WG_CONFIG_FILE, XRAY_BINARY
        if not Path(WG_CONFIG_FILE).exists() or not Path(XRAY_BINARY).exists():
            return None
        from core.runner import which, run
        if not which("wg"):
            return None
        try:
            result = run(["wg", "--version"], capture=True, check=False)
            if result.returncode == 0:
                line = result.stdout.strip()
                for part in line.split():
                    cleaned = part.lstrip("v")
                    if cleaned and cleaned[0].isdigit():
                        return cleaned.rstrip(",")
        except Exception:
            pass
        return "installed"

    def versions(self) -> list[str]:
        return ["latest"]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("wireguard.step.check_os"),
            InstallStep("wireguard.step.install_wg"),
            InstallStep("wireguard.step.install_xray"),
            InstallStep("wireguard.step.gen_keys"),
            InstallStep("wireguard.step.write_xray_config"),
            InstallStep("wireguard.step.write_wg_config"),
            InstallStep("wireguard.step.start_services"),
            InstallStep("wireguard.step.verify"),
        ]

    def install(self, version: str) -> None:
        from wireguard.server import install_server
        install_server()

    def uninstall(self) -> None:
        from wireguard.server import uninstall_server
        uninstall_server()

    def diagnose(self) -> None:
        from wireguard.server import diagnose_server
        diagnose_server()

    def manage(self) -> None:
        from wireguard.server import manage_peers
        manage_peers()


@register
class WgClientRecipe(Recipe):
    key: ClassVar[str] = "wg_client"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "WireGuard 私网客户端（over Xray REALITY）"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    hidden: ClassVar[bool] = True

    def detect(self) -> str | None:
        from core.sysconfig import _load as _sc_load
        entry = _sc_load().get("wg_client", {})
        if entry.get("status") != "installed":
            return None
        from pathlib import Path
        from wireguard.constants import WG_CONFIG_FILE, XRAY_BINARY
        if not Path(WG_CONFIG_FILE).exists() or not Path(XRAY_BINARY).exists():
            return None
        from core.runner import which, run
        if not which("wg"):
            return None
        try:
            result = run(["wg", "--version"], capture=True, check=False)
            if result.returncode == 0:
                line = result.stdout.strip()
                for part in line.split():
                    cleaned = part.lstrip("v")
                    if cleaned and cleaned[0].isdigit():
                        return cleaned.rstrip(",")
        except Exception:
            pass
        return "installed"

    def versions(self) -> list[str]:
        return ["latest"]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("wireguard.step.check_os"),
            InstallStep("wireguard.step.install_wg"),
            InstallStep("wireguard.step.install_xray"),
            InstallStep("wireguard.step.gen_keys"),
            InstallStep("wireguard.step.write_xray_config"),
            InstallStep("wireguard.step.write_wg_config"),
            InstallStep("wireguard.step.config_nm"),
            InstallStep("wireguard.step.add_routes"),
            InstallStep("wireguard.step.verify"),
        ]

    def install(self, version: str) -> None:
        from wireguard.client import install_client
        install_client()

    def uninstall(self) -> None:
        from wireguard.client import uninstall_client
        uninstall_client()

    def diagnose(self) -> None:
        from wireguard.client import diagnose_client
        diagnose_client()

    def manage(self) -> None:
        from wireguard.client import manage_client
        manage_client()
