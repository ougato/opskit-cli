"""Oh My Zsh Recipe（系统工具分类）。"""
from __future__ import annotations

from typing import ClassVar

from software.base import InstallStep, Recipe
from software.recipes.ohmyzsh.constants import P10K_THEME_NAME
from software.recipes.ohmyzsh.impl import (
    detect,
    install_ohmyzsh,
    manage_ohmyzsh,
    uninstall_ohmyzsh,
)
from software.registry import register


@register
class OhMyZshRecipe(Recipe):
    key: ClassVar[str] = "ohmyzsh"
    category: ClassVar[str] = "systools"
    description: ClassVar[str] = "Oh My Zsh + Powerlevel10k 一键配置"
    platforms: ClassVar[list[str]] = ["linux", "darwin"]
    dependencies: ClassVar[list] = []
    requires_root: ClassVar[bool] = False

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = False
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    has_install_version_selection: ClassVar[bool] = False

    def detect(self) -> str | None:
        return detect()

    def versions(self) -> list[str]:
        return [P10K_THEME_NAME]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("ohmyzsh.step.check_env"),
            InstallStep("ohmyzsh.step.install_omz"),
            InstallStep("ohmyzsh.step.disable_update"),
            InstallStep("ohmyzsh.step.config_p10k"),
            InstallStep("ohmyzsh.step.switch_shell"),
        ]

    def install(self, version: str) -> None:
        install_ohmyzsh()

    def uninstall(self, version: str | None = None) -> None:
        uninstall_ohmyzsh()

    def manage(self) -> None:
        manage_ohmyzsh()
