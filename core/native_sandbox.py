"""Platform-neutral native isolation contracts. Never fall back to host execution."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import sys
from typing import Protocol, ContextManager, BinaryIO


class NativeSandboxError(RuntimeError):
    """Isolation could not be established; no plugin may be started."""

    def __init__(self, code: str, **parameters):
        from core.i18n import t
        self.code = code
        super().__init__(t("plugin_native.errors." + code, **parameters))


class GrantChoice(str, Enum):
    ONCE = "once"
    ALWAYS = "always"
    DENY = "deny"


@dataclass(frozen=True)
class NativeLimits:
    """Host-owned process-tree budgets, shared by future platform backends."""

    memory_bytes: int = 256 * 1024**2
    max_processes: int = 16
    cpu_percent: int = 25

    def __post_init__(self):
        if (type(self.memory_bytes) is not int or not 16 * 1024**2 <= self.memory_bytes <= 2 * 1024**3
                or type(self.max_processes) is not int or not 1 <= self.max_processes <= 64
                or type(self.cpu_percent) is not int or not 1 <= self.cpu_percent <= 100):
            raise ValueError("invalid native resource limits")


@dataclass(frozen=True)
class CapabilityRequest:
    """Host-created scope; plugin descriptions never confer authority."""

    plugin: str
    fingerprint: str
    operation: str
    resource: str

    def __post_init__(self):
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.plugin):
            raise ValueError("invalid plugin identity")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("invalid plugin fingerprint")
        if self.operation not in {"directory.read", "directory.write", "directory.read_write", "directory.manage", "tool.execute"}:
            raise ValueError("unsupported host capability")
        if (not isinstance(self.resource, str) or not self.resource
                or len(self.resource.encode("utf-8")) > 4096
                or any(ord(char) < 32 for char in self.resource)):
            raise ValueError("invalid capability resource")


class NativeProcess(Protocol):
    pid: int
    stdin: BinaryIO
    stdout: BinaryIO

    def poll(self) -> int | None: ...

    def wait(self, timeout: float) -> int: ...


class NativeBackend(Protocol):
    platform: str

    def probe(self) -> None:
        """Check native APIs without executing plugin code."""
        ...

    def launch(self, executable: str, args: list[str], *, readonly_trees=(),
               writable_trees=(), stdio=None, environment=None,
               cwd=None, limits: NativeLimits | None = None) -> ContextManager[NativeProcess]:
        """Verify isolation before execution; kill descendants on context exit."""
        ...


def get_backend(platform: str | None = None) -> NativeBackend:
    selected = sys.platform if platform is None else platform
    if selected == "win32":
        from core.windows_appcontainer import WindowsBackend
        return WindowsBackend()
    if selected in {"linux", "darwin"}:
        raise NativeSandboxError("platform_pending", platform=selected)
    raise NativeSandboxError("platform_unsupported")
