"""Host-only capability decisions; plugins cannot write this authority store."""
from __future__ import annotations

from dataclasses import asdict
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys

from core.native_sandbox import CapabilityRequest, GrantChoice
from core.paths import data_dir
from core.resource_lock import ResourceLock

MAX_GRANTS = 256


def _interactive_input():
    """Windows NUL is a character device but cannot accept console input."""
    try:
        if not sys.stdin.isatty():
            return False
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_mode = kernel.GetConsoleMode
            get_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            get_mode.restype = wintypes.BOOL
            mode = wintypes.DWORD()
            return bool(get_mode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(mode)))
        return True
    except (AttributeError, OSError, ValueError):
        return False


class CapabilityDenied(PermissionError):
    def __init__(self, code: str):
        from core.i18n import t
        self.code = code
        super().__init__(t("plugin_native.errors." + code))


def _key(request: CapabilityRequest) -> str:
    content = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _covers(grant: CapabilityRequest, request: CapabilityRequest) -> bool:
    if (grant.plugin, grant.fingerprint, grant.operation) != (request.plugin, request.fingerprint, request.operation):
        return False
    if grant == request:
        return True
    if request.operation != "directory.read":
        return False
    parent, child = Path(grant.resource), Path(request.resource)
    if (not parent.is_absolute() or not child.is_absolute() or ".." in parent.parts or ".." in child.parts
            or any(part.endswith((".", " ")) or ":" in part for part in (*parent.parts[1:], *child.parts[1:]))):
        return False
    try:
        root = os.path.normcase(os.path.normpath(str(parent)))
        target = os.path.normcase(os.path.normpath(str(child)))
        return os.path.commonpath((root, target)) == root
    except ValueError:
        return False


def capability_info_lines(request: CapabilityRequest, *, include_fingerprint: bool = False) -> list[str]:
    """Build the same localized resource rows for consent and permission details."""
    from core.i18n import t
    resource = request.resource
    tool_scope = False
    if request.operation == "tool.execute":
        try:
            details = json.loads(resource)
            tool_scope = isinstance(details, dict) and details.get("authorization_scope") == "tool"
            if tool_scope and not include_fingerprint:
                resource = details["executable"]
        except (ValueError, KeyError, TypeError):
            pass
    lines = [t("plugin_native.operation_line", operation=t("plugin_native.operations." + request.operation)),
             t("plugin_native.resource_line", resource=resource)]
    if include_fingerprint:
        lines.append(t("plugin_native.fingerprint_line", fingerprint=request.fingerprint))
    if request.operation == "directory.read":
        lines.append(t("plugin_native.directory_read_scope"))
    if request.operation == "tool.execute":
        lines.append(t("plugin_native.tool_scope_warning" if tool_scope else "plugin_native.tool_warning"))
    return lines


def prompt_grant(request: CapabilityRequest) -> GrantChoice:
    """Security prompt is owned by the host, and cannot be approved with --yes."""
    from core.cli import non_interactive
    from core.prompt import UserCancel, select
    from core.i18n import t
    from core.theme import get_icon
    if non_interactive.get() or not _interactive_input():
        raise CapabilityDenied("interaction_required")
    answer = select(
        [t("plugin_native.permissions"), request.plugin], t("plugin_native.request"),
        [{"key": "1", "label": f"{get_icon('success')} {t('plugin_native.once')}"},
         {"key": "2", "label": f"{get_icon('plugin_permissions')} {t('plugin_native.always')}"}],
        info_lines=capability_info_lines(request),
        back_label=f"{get_icon('back')} {t('plugin_native.back')}",
    )
    if answer is None:
        raise UserCancel
    return {"1": GrantChoice.ONCE, "2": GrantChoice.ALWAYS}.get(answer, GrantChoice.DENY)


class CapabilityAuthority:
    """One instance per invocation. Once grants expire when this object closes.

    Revocation increments an epoch, invalidating all in-flight once grants too.
    A live identity callback must recheck current plugin trust, not just a cache.
    """

    def __init__(self, *, path: Path | None = None):
        self.path = Path(path) if path is not None else data_dir() / "plugin-capabilities.yaml"
        self.lock = ResourceLock(lambda: self.path.with_suffix(".lock"),
                                 label="plugin capability authority")
        self.once: dict[str, int] = {}
        self.once_scopes: dict[str, CapabilityRequest] = {}
        self.suspend_wait = nullcontext
        self.closed = False

    def _load(self):
        from core.bounded_yaml import load_bounded_yaml
        try:
            self.path.lstat()
        except FileNotFoundError:
            return {"schema_version": 1, "epoch": 0, "grants": {}}
        try:
            value = load_bounded_yaml(self.path)
            if (not isinstance(value, dict) or set(value) != {"schema_version", "epoch", "grants"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or type(value["epoch"]) is not int or value["epoch"] < 0
                    or not isinstance(value["grants"], dict) or len(value["grants"]) > MAX_GRANTS):
                raise ValueError("invalid authority envelope")
            for key, record in value["grants"].items():
                if not isinstance(record, dict) or _key(CapabilityRequest(**record)) != key:
                    raise ValueError("invalid authority grant")
            return value
        except Exception as error:
            raise CapabilityDenied("store_invalid") from error

    def _save(self, value):
        import yaml
        from core.bounded_yaml import parse_bounded_yaml
        from core.secrets import atomic_write
        content = yaml.safe_dump(value, allow_unicode=True, sort_keys=True)
        parse_bounded_yaml(content.encode("utf-8"))
        atomic_write(self.path, content)

    def _live(self, request, check_identity):
        if self.closed or check_identity(request.plugin, request.fingerprint) is not True:
            raise CapabilityDenied("identity_revoked")

    def _authorized(self, request, state):
        key = _key(request)
        if key in state["grants"] or self.once.get(key) == state["epoch"]:
            return True
        if request.operation != "directory.read":
            return False
        return (any(_covers(CapabilityRequest(**item), request) for item in state["grants"].values())
                or any(self.once.get(key) == state["epoch"] and _covers(item, request)
                       for key, item in self.once_scopes.items()))

    def require(self, request: CapabilityRequest, *, check_identity, prompt=prompt_grant):
        self._live(request, check_identity)
        key = _key(request)
        with self.lock.hold():
            state = self._load()
            epoch = state["epoch"]
            if self._authorized(request, state):
                self._live(request, check_identity)
                return
        # Do not hold the store lock while waiting for human input.
        with self.suspend_wait():
            choice = prompt(request)
        if not isinstance(choice, GrantChoice) or choice is GrantChoice.DENY:
            raise CapabilityDenied("denied")
        with self.lock.hold():
            state = self._load()
            self._live(request, check_identity)
            if state["epoch"] != epoch:
                raise CapabilityDenied("changed_during_prompt")
            if choice is GrantChoice.ALWAYS:
                if key not in state["grants"] and len(state["grants"]) >= MAX_GRANTS:
                    raise CapabilityDenied("grant_limit")
                state["grants"][key] = asdict(request)
                self._save(state)
            else:
                if key not in self.once and len(self.once) >= MAX_GRANTS:
                    raise CapabilityDenied("grant_limit")
                self.once[key] = epoch
                self.once_scopes[key] = request

    def check(self, request: CapabilityRequest, *, check_identity) -> None:
        """Revalidate before/during privileged work; never opens a prompt."""
        self._live(request, check_identity)
        with self.lock.hold():
            state = self._load()
            if not self._authorized(request, state):
                raise CapabilityDenied("missing_or_revoked")

    def list_grants(self) -> list[CapabilityRequest]:
        with self.lock.hold():
            return [CapabilityRequest(**value) for value in self._load()["grants"].values()]

    def revoke(self, request: CapabilityRequest) -> None:
        with self.lock.hold():
            state = self._load()
            state["grants"].pop(_key(request), None)
            state["epoch"] += 1
            self._save(state)
        self.once.clear()
        self.once_scopes.clear()

    def close(self):
        self.once.clear()
        self.once_scopes.clear()
        self.closed = True
