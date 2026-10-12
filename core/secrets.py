"""Central redaction and private atomic persistence."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

SENSITIVE = re.compile(r"password|passwd|secret|token|authorization|cookie|access.?key|private.?key", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if SENSITIVE.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", value)
        value = re.sub(r"(?i)((?:password|passwd|secret|token|authorization|cookie|access[_-]?key|secret[_-]?key)\s*[=:]\s*)[^\s,;&]+", r"\1[redacted]", value)
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = "[exception details redacted]"
        return True


def before_send(event, hint):
    event.pop("request", None)
    event.pop("user", None)
    for values in event.get("exception", {}).get("values", []):
        values["value"] = "[redacted]"
        for frame in values.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
            frame.pop("filename", None)
            frame.pop("abs_path", None)
    return redact(event)


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".opskit-", dir=path.parent)
    try:
        restrict_permissions(Path(temporary))
        with os.fdopen(fd, "wb") as output:
            output.write(content.encode("utf-8") if isinstance(content, str) else content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restrict_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
    convert.restype = wintypes.BOOL
    apply = advapi.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    flags = "OICI" if path.is_dir() else ""
    if not convert(f"D:P(A;{flags};FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not apply(str(path), 0x00000004 | 0x80000000, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(descriptor)
