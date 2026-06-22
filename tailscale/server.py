"""Tailscale 安装、卸载、诊断与管理。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from rich.console import Console

from core.i18n import t
from core.progress import MultiStepProgress
from core.prompt import UserCancel, pause, select
from core.theme import print_error, print_info, print_success
from software.base import InstallError, UninstallError
from tailscale.constants import (
    APT_ASSUME_YES_ARG,
    APT_GET_COMMAND,
    APT_PURGE_COMMAND,
    BASH_COMMAND,
    DEBIAN_FRONTEND_ENV,
    DEBIAN_FRONTEND_NONINTERACTIVE,
    SYSTEMCTL_COMMAND,
    TAILSCALE_COMMAND,
    TAILSCALE_COMMAND_TIMEOUT_SECONDS,
    TAILSCALE_HOSTNAME,
    TAILSCALE_INSTALL_SCRIPT_URL,
    TAILSCALE_INSTALL_TIMEOUT_SECONDS,
    TAILSCALE_KEYRING_FILE,
    TAILSCALE_LINUX_PLATFORMS,
    TAILSCALE_PACKAGES,
    TAILSCALE_REPO_FILE,
    TAILSCALE_RUN_DIR,
    TAILSCALE_STATE_DIR,
    TAILSCALE_UP_TIMEOUT_SECONDS,
    TAILSCALED_SERVICE,
)

console = Console()


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _ensure_linux() -> None:
    if sys.platform not in TAILSCALE_LINUX_PLATFORMS:
        raise InstallError(t("tailscale.error.unsupported_os"))


def _run(command: list[str], check: bool = True, timeout: int = TAILSCALE_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check, capture_output=True, text=True, timeout=timeout)


def _run_root(command: list[str], check: bool = True, timeout: int = TAILSCALE_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    from core.privilege import run_as_root

    return run_as_root(command, check=check, capture_output=True, text=True, timeout=timeout)


def detect_tailscale_version() -> str | None:
    if not command_exists(TAILSCALE_COMMAND):
        return None
    result = _run([TAILSCALE_COMMAND, "version"], check=False)
    if result.returncode != 0:
        return None
    first = result.stdout.splitlines()[0].strip() if result.stdout else ""
    return first or None


def tailscale_ip() -> str:
    if not command_exists(TAILSCALE_COMMAND):
        return ""
    result = _run([TAILSCALE_COMMAND, "ip", "-4"], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def is_service_active() -> bool:
    result = _run([SYSTEMCTL_COMMAND, "is-active", TAILSCALED_SERVICE], check=False)
    return result.stdout.strip() == "active"


def _install_script() -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as script:
        script_path = Path(script.name)
    try:
        with urllib.request.urlopen(TAILSCALE_INSTALL_SCRIPT_URL, timeout=TAILSCALE_COMMAND_TIMEOUT_SECONDS) as resp:
            script_path.write_bytes(resp.read())
        script_path.chmod(0o700)
        _run_root(
            [BASH_COMMAND, str(script_path)],
            timeout=TAILSCALE_INSTALL_TIMEOUT_SECONDS,
        )
    finally:
        script_path.unlink(missing_ok=True)


def start_login() -> str:
    command = [TAILSCALE_COMMAND, "up", "--hostname", TAILSCALE_HOSTNAME]
    try:
        result = _run_root(command, check=False, timeout=TAILSCALE_UP_TIMEOUT_SECONDS)
        return (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired as exc:
        output = ""
        if exc.stdout:
            output += exc.stdout.decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else exc.stdout
        if exc.stderr:
            output += exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else exc.stderr
        return output.strip()


def install_client() -> None:
    step_descs = [t("tailscale.step.check_os"), t("tailscale.step.install"), t("tailscale.step.start"), t("tailscale.step.login")]
    with MultiStepProgress(step_descs) as sp:
        sp.step(t("tailscale.step.check_os"))
        _ensure_linux()

        sp.step(t("tailscale.step.install"))
        if not command_exists(TAILSCALE_COMMAND):
            _install_script()

        sp.step(t("tailscale.step.start"))
        _run_root([SYSTEMCTL_COMMAND, "enable", "--now", TAILSCALED_SERVICE], check=False)

        sp.step(t("tailscale.step.login"))
        login_output = start_login()

    print_success(t("tailscale.output.install_done"))
    if login_output:
        console.print(login_output)
    ip = tailscale_ip()
    if ip:
        console.print(f"{t('tailscale.output.ip')}: {ip}")
    pause()


def uninstall_client() -> None:
    try:
        if command_exists(TAILSCALE_COMMAND):
            _run_root([TAILSCALE_COMMAND, "down"], check=False)
        _run_root([SYSTEMCTL_COMMAND, "disable", "--now", TAILSCALED_SERVICE], check=False)
        if command_exists(APT_GET_COMMAND):
            env = {**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE}
            subprocess.run(
                [APT_GET_COMMAND, APT_PURGE_COMMAND, APT_ASSUME_YES_ARG, *TAILSCALE_PACKAGES],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=TAILSCALE_INSTALL_TIMEOUT_SECONDS,
            )
        remove_tailscale_artifacts()
    except Exception as exc:
        raise UninstallError(str(exc)) from exc
    print_success(t("tailscale.output.uninstall_done"))
    pause()


def remove_tailscale_artifacts() -> None:
    for path in (TAILSCALE_STATE_DIR, TAILSCALE_RUN_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    for path in (TAILSCALE_REPO_FILE, TAILSCALE_KEYRING_FILE):
        path.unlink(missing_ok=True)


def diagnose_client() -> None:
    print_info(t("tailscale.diagnose.title"))
    console.print(f"{t('tailscale.diagnose.installed')}: {detect_tailscale_version() or False}")
    console.print(f"{t('tailscale.diagnose.service')}: {is_service_active()}")
    ip = tailscale_ip()
    console.print(f"{t('tailscale.output.ip')}: {ip or '-'}")
    result = _run([TAILSCALE_COMMAND, "status"], check=False) if command_exists(TAILSCALE_COMMAND) else None
    if result and result.stdout:
        console.print(result.stdout.strip())
    pause()


def manage_client() -> None:
    while True:
        try:
            key = select(
                breadcrumb=["OpsKit", t("menu.software"), t("software.tailscale"), t("software.manage")],
                subtitle=t("prompt.select"),
                choices=[
                    {"key": "1", "label": t("tailscale.manage.status")},
                    {"key": "2", "label": t("tailscale.manage.login")},
                    {"key": "3", "label": t("tailscale.manage.down")},
                    {"key": "4", "label": t("tailscale.manage.restart")},
                ],
                theme_key="software",
            )
        except UserCancel:
            return
        if key is None:
            return
        if key == "1":
            diagnose_client()
        elif key == "2":
            output = start_login()
            if output:
                console.print(output)
            pause()
        elif key == "3":
            _run_root([TAILSCALE_COMMAND, "down"], check=False)
            print_success(t("tailscale.output.down_done"))
            pause()
        elif key == "4":
            _run_root([SYSTEMCTL_COMMAND, "restart", TAILSCALED_SERVICE], check=False)
            print_success(t("tailscale.output.restart_done"))
            pause()
