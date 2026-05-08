"""WireGuard Linux 平台驱动"""
from __future__ import annotations

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver


class LinuxDriver(PlatformDriver):

    def check_compat(self) -> None:
        from core.platform import get_platform
        info = get_platform()
        if info.os_type != "linux":
            raise InstallError(t("software.wireguard_linux_only"))
