"""Read-only Windows helper identity, independent of cached scheduling intent."""
import os
from pathlib import Path

JOB_FILES = {"job.json", "artifact.exe", "apply.ps1", "result.json", "running.json", "recovery.json"}


def _creation_ticks(pid):
    """Exact native FILETIME, avoiding rounded epoch times and PID reuse."""
    if os.name != "nt":
        raise OSError("native Windows process identity unavailable")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        if ctypes.get_last_error() == 87:  # No such PID.
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                     ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exited.dwHighDateTime or exited.dwLowDateTime:
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def _same_path(left, right):
    # Cached declarations must not trigger filesystem/UNC resolution.
    return os.path.normcase(os.path.abspath(os.path.normpath(left))) == os.path.normcase(os.path.abspath(os.path.normpath(right)))


def helper_status(job, metadata):
    """Return a snapshot; never signal, wait for, or modify the claimed process."""
    from core.updater import _read_update_record
    import psutil
    job = Path(job)
    try:
        ack = _read_update_record(job / "running.json")
    except FileNotFoundError:
        return "unknown"
    if (type(ack.get("schema_version")) is not int or ack["schema_version"] != 1
            or ack.get("job_id") != job.name or metadata.get("job_id") != job.name
            or type(ack.get("version")) is not int or ack["version"] != metadata.get("version")
            or type(ack.get("pid")) is not int or not 0 < ack["pid"] <= 0xffffffff
            or type(ack.get("created_ticks")) is not int or ack["created_ticks"] <= 0
            or any(not isinstance(ack.get(key), str) or not 0 < len(ack[key]) <= 8192
                   or not Path(ack[key]).is_absolute()
                   for key in ("script", "executable"))):
        raise ValueError("invalid update helper acknowledgement")
    expected_executable = metadata.get("helper_executable")
    if not isinstance(expected_executable, str) or not expected_executable:
        return "unknown"
    if len(expected_executable) > 8192 or not Path(expected_executable).is_absolute():
        raise ValueError("invalid update helper executable")
    if "helper_pid" in metadata and (type(metadata["helper_pid"]) is not int or metadata["helper_pid"] <= 0):
        raise ValueError("invalid update helper process identity")
    if (not _same_path(ack["script"], job / "apply.ps1")
            or not _same_path(ack["executable"], expected_executable)):
        return "identity_mismatch"
    if "helper_pid" in metadata and metadata["helper_pid"] != ack["pid"]:
        return "identity_mismatch"
    try:
        created = _creation_ticks(ack["pid"])
        if created is None:
            return "exited"
        if created != ack["created_ticks"]:
            return "identity_mismatch"
        process = psutil.Process(ack["pid"])
        if not _same_path(process.exe(), expected_executable):
            return "identity_mismatch"
        arguments = process.cmdline()
        # Match a real -File argument pair, not a substring of arbitrary argv.
        if not any(value.lower() == "-file" and index + 1 < len(arguments)
                   and _same_path(arguments[index + 1], job / "apply.ps1")
                   for index, value in enumerate(arguments)):
            return "identity_mismatch"
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return "exited"
        # Recheck after querying argv/image so a reused PID cannot pass by race.
        latest = _creation_ticks(ack["pid"])
        if latest is None:
            return "exited"
        return "alive" if latest == created else "identity_mismatch"
    except psutil.NoSuchProcess:
        return "exited"
    except (OSError, psutil.AccessDenied):
        return "unknown"
