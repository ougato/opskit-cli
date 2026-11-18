"""Owned AppContainer profile leases and explicit crash recovery.

Only confirmed creations can be removed. Creation intents whose confirmation
was lost are retained; random names or derived SIDs do not confer ownership.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from core.paths import data_dir
from core.plugin_runtime_inventory import _read_file
from core.plugin_state import _directory, _valid_identity
from core.resource_lock import ResourceLock
from core.secrets import atomic_write
from core.native_sandbox import NativeSandboxError
import stat

MAX_RECORD = 8192
MAX_ENTRIES = 4096


def _root():
    if os.name != "nt":
        raise NativeSandboxError("windows_required")
    return data_dir().absolute() / "native-profile-leases"


def _path(token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid native profile lease")
    return _root() / (token + ".json")


def _sid_text(sid):
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    convert.restype = wintypes.BOOL
    text = wintypes.LPWSTR()
    if not convert(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(ctypes.cast(text, ctypes.c_void_p))


def _folder(sid):
    api = ctypes.WinDLL("userenv", use_last_error=True)
    folder = api.GetAppContainerFolderPath
    folder.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)]
    folder.restype = ctypes.c_long
    text = wintypes.LPWSTR()
    result = folder(sid, ctypes.byref(text))
    if result < 0:
        raise OSError("native profile directory unavailable")
    try:
        path = Path(text.value)
        for parent in (path, *path.parents):
            _directory(parent)
        return path, _directory(path)
    finally:
        free = ctypes.WinDLL("ole32").CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free(ctypes.cast(text, ctypes.c_void_p))


def _read(token):
    path = _path(token)
    raw = _read_file(path, Path(path.anchor), MAX_RECORD)
    from core.plugin_json import loads
    value = loads(raw.decode("utf-8"))
    if (not isinstance(value, dict) or set(value) != {"schema_version", "name", "phase", "sid", "folder", "identity"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["name"] != "OpsKit.Plugin." + token
            or value["phase"] not in {"creating", "created", "removed"}):
        raise ValueError("invalid native profile record")
    if value["phase"] == "creating":
        if any(value[key] is not None for key in ("sid", "folder", "identity")):
            raise ValueError("invalid unconfirmed profile record")
    elif (not isinstance(value["sid"], str) or re.fullmatch(r"S-1-15-2(?:-[0-9]+){7}", value["sid"]) is None
          or not isinstance(value["folder"], str) or not Path(value["folder"]).is_absolute()
          or not _valid_identity(value["identity"])):
        raise ValueError("invalid confirmed profile record")
    return value, hashlib.sha256(raw).hexdigest()


def _write(token, value):
    atomic_write(_path(token), json.dumps(value, sort_keys=True))


def _retire_lock(token):
    """No profile record remains; retire only an ordinary empty lease file."""
    lock = _path(token).with_suffix(".lock")
    try:
        info = lock.lstat()
        if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == 0
                and not getattr(info, "st_file_attributes", 0) & 0x400):
            lock.unlink()
    except (FileNotFoundError, PermissionError):
        # A simultaneous inspector may still own the handle; do not interfere.
        pass


def _delete_and_retire(token, value, api):
    current, _ = _read(token)
    if current != value:
        raise ValueError("native profile journal changed during cleanup")
    result = api.DeleteAppContainerProfile(value["name"])
    if result < 0:
        raise NativeSandboxError("profile_cleanup_failed", status=f"0x{result & 0xffffffff:08x}")
    if value["phase"] == "created":
        # Persist the successful API result before retiring the journal. A retry
        # of this phase touches only the journal, never a profile with this name.
        _write(token, dict(value, phase="removed"))
    _path(token).unlink()


@contextmanager
def managed_profile(api, free):
    token = uuid.uuid4().hex
    path = _path(token)
    lease = ResourceLock(lambda: path.with_suffix(".lock"), label="native profile")
    with lease.hold():
        value = {"schema_version": 1, "name": "OpsKit.Plugin." + token,
                 "phase": "creating", "sid": None, "folder": None, "identity": None}
        _write(token, value)
        sid = ctypes.c_void_p()
        result = api.CreateAppContainerProfile(value["name"], value["name"], "OpsKit isolated plugin",
                                               None, 0, ctypes.byref(sid))
        if result < 0:
            # Existing/unknown profiles must never be adopted for recovery.
            raise NativeSandboxError("profile_create_failed", status=f"0x{result & 0xffffffff:08x}")
        try:
            if not sid.value:
                raise NativeSandboxError("profile_sid_missing")
            text = _sid_text(sid)
            folder, identity = _folder(text)
            value.update(phase="created", sid=text, folder=str(folder), identity=identity)
            _write(token, value)
            yield value["name"], sid
        finally:
            if sid.value:
                free(sid)
            _delete_and_retire(token, value, api)
    _retire_lock(token)


def inspect(*, limit=256):
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError("invalid native profile inspection limit")
    root = _root()
    for parent in reversed((root, *root.parents)):
        try:
            _directory(parent)
        except FileNotFoundError:
            return []
    values = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_ENTRIES:
                raise ValueError("native profile inspection limit exceeded")
            if not entry.name.endswith(".json"):
                continue
            if len(values) >= limit:
                raise ValueError("native profile result limit exceeded")
            token = entry.name[:-5]
            try:
                value, digest = _read(token)
                values.append({"transaction": token, "phase": value["phase"],
                               "profile": value["name"], "record_sha256": digest, "valid": True})
            except (OSError, ValueError, RuntimeError):
                values.append({"transaction": token, "valid": False})
    return sorted(values, key=lambda value: value["transaction"])


def recover(token, digest):
    path = _path(token)
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid native profile record digest")
    with ResourceLock(lambda: path.with_suffix(".lock"), label="native profile").hold():
        value, observed = _read(token)
        if observed != digest or value["phase"] not in {"created", "removed"}:
            raise ValueError("unconfirmed or changed native profile")
        if value["phase"] == "removed":
            # Deletion was already confirmed; do not resolve or delete resources.
            path.unlink()
        else:
            _recover_created(token, value, digest)
    _retire_lock(token)
    return {"transaction": token, "removed": True}


def _recover_created(token, value, digest):
    from core.windows_appcontainer import WindowsBackend
    api, free = WindowsBackend()._api()
    derive = api.DeriveAppContainerSidFromAppContainerName
    derive.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    derive.restype = ctypes.c_long
    sid = ctypes.c_void_p()
    result = derive(value["name"], ctypes.byref(sid))
    if result < 0:
        raise OSError("native profile identity unavailable")
    try:
        if _sid_text(sid) != value["sid"]:
            raise ValueError("native profile identity changed")
    finally:
        if sid.value:
            free(sid)
    folder, identity = _folder(value["sid"])
    if str(folder) != value["folder"] or identity != value["identity"]:
        raise ValueError("native profile directory changed")
    current, observed = _read(token)
    if current != value or observed != digest:
        raise ValueError("native profile record changed")
    _delete_and_retire(token, value, api)
