"""Verified TCP tuning; preserve the host's queue and roll back failed changes."""
from __future__ import annotations

from core.privilege import run_as_root, write_root_file
from software.base import InstallError
from xui.constants import (
    BBR_KERNEL_MODULE, BBR_MODULES_FILE, BBR_SYSCTL_FILE,
    BBR_SYSCTL_FILE_CONTENT, BBR_SYSPARAMS, SERVICE_RESTART_TIMEOUT,
    MODPROBE_COMMAND, SYSCTL_COMMAND, SYSCTL_WRITE_ARG,
)


def _run(command, *, check=True):
    return run_as_root(command, check=check, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=SERVICE_RESTART_TIMEOUT)


def _read(param):
    return _run([SYSCTL_COMMAND, "-n", param]).stdout.strip()


def _read_config(path) -> bytes | None:
    path = str(path)
    if _run(["test", "-f", path], check=False).returncode == 1:
        return None
    # Preserve the original bytes, including whether the file ends in a newline.
    limit = 65536
    result = run_as_root(
        ["head", "-c", str(limit + 1), "--", path],
        check=True, capture_output=True, timeout=SERVICE_RESTART_TIMEOUT,
    )
    if len(result.stdout) > limit:
        raise OSError(f"{path} exceeds the configuration size limit")
    return result.stdout


def enable() -> None:
    files = {BBR_SYSCTL_FILE: BBR_SYSCTL_FILE_CONTENT,
             BBR_MODULES_FILE: BBR_KERNEL_MODULE + "\n"}
    previous = {}
    written = []
    changed = []
    originals = {}
    try:
        # BBR may be built into the kernel; modprobe alone is not a capability test.
        if "bbr" not in _read("net.ipv4.tcp_available_congestion_control").split():
            _run([MODPROBE_COMMAND, BBR_KERNEL_MODULE], check=False)
        if "bbr" not in _read("net.ipv4.tcp_available_congestion_control").split():
            raise RuntimeError("kernel does not provide BBR")
        originals = {key: _read(key) for key in BBR_SYSPARAMS}
        previous = {path: _read_config(path) for path in files}
        for key, value in BBR_SYSPARAMS.items():
            changed.append(key)
            _run([SYSCTL_COMMAND, SYSCTL_WRITE_ARG, f"{key}={value}"])
            if _read(key) != value:
                raise RuntimeError(f"{key} did not take effect")
        for path, content in files.items():
            written.append(path)
            write_root_file(path, content, "0644")
            if _read_config(path) != content.encode("utf-8"):
                raise RuntimeError(f"{path} was not persisted")
    except Exception as exc:
        failures = []
        for path in reversed(written):
            try:
                if previous[path] is None:
                    _run(["rm", "-f", str(path)])
                else:
                    write_root_file(path, previous[path], "0644")
                if _read_config(path) != previous[path]:
                    raise RuntimeError("configuration rollback did not take effect")
            except Exception:
                failures.append(str(path))
        for key in reversed(changed):
            try:
                _run([SYSCTL_COMMAND, SYSCTL_WRITE_ARG, f"{key}={originals[key]}"])
                if _read(key) != originals[key]:
                    raise RuntimeError("parameter rollback did not take effect")
            except Exception:
                failures.append(key)
        detail = f"; rollback incomplete: {', '.join(failures)}" if failures else ""
        raise InstallError(f"XUI TCP tuning failed: {exc}{detail}") from exc
