"""服务管理兼容层。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SYSTEMCTL_COMMAND = "systemctl"
SERVICE_COMMAND = "service"
UPDATE_RC_COMMAND = "update-rc.d"
CHKCONFIG_COMMAND = "chkconfig"
SYSTEMCTL_TIMEOUT_SECONDS = 5
SERVICE_TIMEOUT_SECONDS = 20
SYSTEMD_RUNTIME_DIR = Path("/run/systemd/system")
SYSTEMD_ACTIVE_STATES = {"running", "degraded", "maintenance", "starting", "initializing"}


def systemd_is_available() -> bool:
    if not shutil.which(SYSTEMCTL_COMMAND) or not SYSTEMD_RUNTIME_DIR.exists():
        return False
    try:
        result = subprocess.run(
            [SYSTEMCTL_COMMAND, "is-system-running"],
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SECONDS,
        )
    except Exception:
        return False
    state = result.stdout.strip()
    return result.returncode == 0 or state in SYSTEMD_ACTIVE_STATES


def sysv_service_is_available() -> bool:
    return shutil.which(SERVICE_COMMAND) is not None


def enable_now(service_name: str) -> None:
    from core.privilege import run_as_root

    if systemd_is_available():
        run_as_root(
            [SYSTEMCTL_COMMAND, "enable", "--now", service_name],
            capture_output=True,
            text=True,
            check=True,
            timeout=SERVICE_TIMEOUT_SECONDS,
        )
        return
    if sysv_service_is_available():
        run_as_root(
            [SERVICE_COMMAND, service_name, "start"],
            capture_output=True,
            text=True,
            check=True,
            timeout=SERVICE_TIMEOUT_SECONDS,
        )
        if shutil.which(UPDATE_RC_COMMAND):
            run_as_root([UPDATE_RC_COMMAND, service_name, "defaults"], capture_output=True, text=True, check=False)
        elif shutil.which(CHKCONFIG_COMMAND):
            run_as_root([CHKCONFIG_COMMAND, service_name, "on"], capture_output=True, text=True, check=False)
        return
    run_as_root([service_name], capture_output=True, text=True, check=True, timeout=SERVICE_TIMEOUT_SECONDS)


def disable_now(service_name: str) -> None:
    from core.privilege import run_as_root

    if systemd_is_available():
        run_as_root([SYSTEMCTL_COMMAND, "disable", "--now", service_name], capture_output=True, text=True, check=False)
        return
    if sysv_service_is_available():
        run_as_root([SERVICE_COMMAND, service_name, "stop"], capture_output=True, text=True, check=False)
        if shutil.which(UPDATE_RC_COMMAND):
            run_as_root([UPDATE_RC_COMMAND, "-f", service_name, "remove"], capture_output=True, text=True, check=False)
        elif shutil.which(CHKCONFIG_COMMAND):
            run_as_root([CHKCONFIG_COMMAND, service_name, "off"], capture_output=True, text=True, check=False)
