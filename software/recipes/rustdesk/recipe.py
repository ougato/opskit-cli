"""RustDesk Server Recipe。"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import ClassVar

from core.service import systemd_is_available
from software.base import InstallStep, Recipe
from software.disk import known_path_or_none
from software.registry import register
from .constants import (
    RUSTDESK_DATA_DIR,
    RUSTDESK_INSTALL_DIR,
    RUSTDESK_INSTALLED_VERSION,
    RUSTDESK_LOG_DIR,
    RUSTDESK_STATE_DIR,
    RUSTDESK_SYSTEMD_DIR,
    RUSTDESK_VERSION_LATEST,
)
from .server import detect_version, diagnose_server, install_server, uninstall_server


@register
class RustDeskRecipe(Recipe):
    key: ClassVar[str] = "rustdesk"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "RustDesk 远程桌面服务"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = []
    requires_root: ClassVar[bool] = True

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_install_version_selection: ClassVar[bool] = False
    confirm_before_install: ClassVar[bool] = False
    confirm_before_uninstall: ClassVar[bool] = False

    def disk_check_paths(self) -> tuple[Path | None, ...]:
        temporary = known_path_or_none(lambda: Path(tempfile.gettempdir()))
        paths = (
            RUSTDESK_INSTALL_DIR,
            RUSTDESK_DATA_DIR,
            RUSTDESK_LOG_DIR,
            RUSTDESK_STATE_DIR,
            temporary,
        )
        if systemd_is_available():
            return (*paths, RUSTDESK_SYSTEMD_DIR)
        return paths

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
        ]

    def install(self, version: str) -> None:
        install_server(version)

    def uninstall(self, version: str | None = None) -> None:
        uninstall_server()

    def diagnose(self) -> None:
        diagnose_server()

    def diagnostic_report(self, *, timeout=30) -> dict:
        from .server import diagnostic_report
        return diagnostic_report(timeout=timeout)

    def system_version(self) -> str | None:
        return RUSTDESK_INSTALLED_VERSION if detect_version() else None
