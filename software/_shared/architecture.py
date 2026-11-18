"""Reject incompatible binary packages before download or filesystem mutation."""
import platform

from software.base import InstallError


def require_windows_x64(product: str) -> None:
    """Fixed x64 packages require a known x64 host; do not assume emulation."""
    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        raise InstallError(f"{product} Windows package requires x64; CPU architecture: {machine!r}")
