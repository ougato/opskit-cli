"""ClickHouse macOS driver."""
from __future__ import annotations

from .linux import LinuxDriver


class DarwinDriver(LinuxDriver):
    """The official common-static tarball layout works the same on macOS."""
