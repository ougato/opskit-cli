"""ClickHouse platform driver factory."""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path


class PlatformDriver(ABC):
    @abstractmethod
    def install_tarball(self, version: str, tarball: Path) -> str:
        ...

    @abstractmethod
    def install_shim(self, fallback_bin: str) -> None:
        ...

    @abstractmethod
    def remove_shim(self) -> None:
        ...

    @abstractmethod
    def shim_active(self) -> bool:
        ...

    @abstractmethod
    def apply_version_link(self, bin_dir: str) -> None:
        ...

    @abstractmethod
    def restore_original(self) -> None:
        ...

    @abstractmethod
    def detect_active(self) -> str | None:
        ...

    @abstractmethod
    def snapshot_pre_install(self) -> dict:
        ...


def get_driver() -> PlatformDriver:
    if sys.platform == "darwin":
        from .darwin import DarwinDriver
        return DarwinDriver()
    from .linux import LinuxDriver
    return LinuxDriver()
