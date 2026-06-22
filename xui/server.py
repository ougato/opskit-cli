"""x-ui 服务端安装、诊断与管理。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from rich.console import Console

from core.i18n import t
from core.progress import MultiStepProgress
from core.prompt import UserCancel, confirm, pause, select, text_input
from core.theme import print_error, print_info, print_success, print_warning
from software.base import InstallError, UninstallError
from xui.constants import (
    APT_ASSUME_YES_ARG,
    APT_INSTALL_COMMAND,
    APT_UPDATE_COMMAND,
    DEBIAN_FRONTEND_ENV,
    DEBIAN_FRONTEND_NONINTERACTIVE,
    DEFAULT_PANEL_BASE_PATH,
    DEFAULT_PANEL_PORT,
    DEFAULT_PANEL_USER,
    DEFAULT_REALITY_SNI,
    DEFAULT_TROJAN_ENABLE,
    DEFAULT_TROJAN_PORT,
    DEFAULT_TROJAN_REMARK,
    DEFAULT_VLESS_PORT,
    DEFAULT_VLESS_REMARK,
    DEFAULT_XHTTP_PATH_PREFIX,
    APT_GET_COMMAND,
    CURL_COMMAND,
    HTTP_URL_TEMPLATE,
    JOURNALCTL_COMMAND,
    JOURNAL_NO_PAGER_ARG,
    LINUX_PLATFORMS,
    LOOPBACK_HOST,
    SQLITE3_COMMAND,
    SQLITE_YUM_PACKAGE,
    YUM_COMMAND,
    YUM_INSTALL_COMMAND,
    XUI_COMMAND,
    XUI_LOG_LINES,
    XUI_PENDING_INBOUNDS_FILE,
    XUI_STATE_FILE,
    XUI_SERVICE,
    XUI_VERSION_LATEST,
)
from xui.links import build_trojan_link, build_vless_link
from xui.templates import to_xui_api_payload, trojan_inbound, vless_reality_xhttp_inbound
from xui.utils import (
    add_inbound,
    command_exists,
    configure_panel_settings,
    detect_public_host,
    enable_inbound_clients,
    ensure_trojan_certificate,
    generate_reality_keypair,
    gen_password,
    gen_short_id,
    gen_uuid,
    gen_xhttp_path,
    is_port_listening,
    is_service_active,
    load_state,
    redact_state,
    restart_service,
    stop_and_disable_service,
    write_secret_json,
    install_xui_script,
)

console = Console()


def _read_text(breadcrumb: list[str], key: str, default: str) -> str:
    value = text_input(
        breadcrumb=breadcrumb,
        prompt=t(key, default=default),
        default=default,
        theme_key="software",
    )
    return value.strip() or default


def _read_int(breadcrumb: list[str], key: str, default: int) -> int:
    while True:
        raw = _read_text(breadcrumb, key, str(default))
        try:
            value = int(raw)
        except ValueError:
            print_warning(t("xui.error.invalid_port"))
            continue
        if value > 0:
            return value
        print_warning(t("xui.error.invalid_port"))


def _read_bool(breadcrumb: list[str], key: str, default: bool) -> bool:
    if default:
        return confirm(breadcrumb=breadcrumb, prompt=t(key))
    return confirm(breadcrumb=breadcrumb, prompt=t(key))


def _ensure_linux() -> None:
    if sys.platform not in LINUX_PLATFORMS:
        raise InstallError(t("xui.error.unsupported_os"))


def _install_base_packages() -> None:
    if command_exists(CURL_COMMAND) and command_exists(SQLITE3_COMMAND):
        return
    env = {**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE}
    if command_exists(APT_GET_COMMAND):
        subprocess.run(
            [APT_GET_COMMAND, APT_UPDATE_COMMAND],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        subprocess.run(
            [APT_GET_COMMAND, APT_INSTALL_COMMAND, APT_ASSUME_YES_ARG, CURL_COMMAND, SQLITE3_COMMAND],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        return
    if command_exists(YUM_COMMAND):
        subprocess.run(
            [YUM_COMMAND, YUM_INSTALL_COMMAND, APT_ASSUME_YES_ARG, CURL_COMMAND, SQLITE_YUM_PACKAGE],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        return
    raise InstallError(t("xui.error.package_manager_missing"))


def _save_pending_inbounds(inbounds: list[dict[str, object]]) -> None:
    XUI_PENDING_INBOUNDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    XUI_PENDING_INBOUNDS_FILE.write_text(
        json.dumps(inbounds, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _create_inbounds(
    *,
    panel_port: int,
    panel_user: str,
    panel_password: str,
    panel_base_path: str,
    inbounds: list[dict[str, object]],
) -> bool:
    ok = True
    payloads = [to_xui_api_payload(inbound) for inbound in inbounds]
    for payload in payloads:
        if not add_inbound(panel_port, panel_user, panel_password, panel_base_path, payload):
            ok = False
    if not ok:
        _save_pending_inbounds(inbounds)
    else:
        enable_inbound_clients([str(inbound["remark"]) for inbound in inbounds])
    return ok


def install_server() -> None:
    breadcrumb = ["OpsKit", t("menu.software"), t("software.xui"), t("software.install")]
    host_default = detect_public_host()
    panel_port = _read_int(breadcrumb, "xui.input.panel_port", DEFAULT_PANEL_PORT)
    panel_user = _read_text(breadcrumb, "xui.input.panel_user", DEFAULT_PANEL_USER)
    panel_password = _read_text(breadcrumb, "xui.input.panel_password", gen_password())
    panel_base_path = _read_text(breadcrumb, "xui.input.panel_base_path", DEFAULT_PANEL_BASE_PATH)
    host = _read_text(breadcrumb, "xui.input.host", host_default)
    vless_port = _read_int(breadcrumb, "xui.input.vless_port", DEFAULT_VLESS_PORT)
    sni = _read_text(breadcrumb, "xui.input.sni", DEFAULT_REALITY_SNI)
    dest = _read_text(breadcrumb, "xui.input.dest", f"{sni}:{DEFAULT_VLESS_PORT}")
    xhttp_path = _read_text(breadcrumb, "xui.input.xhttp_path", gen_xhttp_path(DEFAULT_XHTTP_PATH_PREFIX))
    enable_trojan = _read_bool(breadcrumb, "xui.input.enable_trojan", DEFAULT_TROJAN_ENABLE)
    trojan_port = DEFAULT_TROJAN_PORT
    if enable_trojan:
        trojan_port = _read_int(breadcrumb, "xui.input.trojan_port", DEFAULT_TROJAN_PORT)

    if not confirm(breadcrumb=breadcrumb, prompt=t("xui.confirm.install")):
        return

    step_keys = [
        "xui.step.check_os",
        "xui.step.install_deps",
        "xui.step.install_xui",
        "xui.step.generate_credentials",
        "xui.step.configure_panel",
        "xui.step.create_vless_xhttp",
        "xui.step.start_service",
        "xui.step.verify",
        "xui.step.print_links",
    ]
    if enable_trojan:
        step_keys.insert(6, "xui.step.create_trojan")
    step_descs = [t(key) for key in step_keys]

    with MultiStepProgress(step_descs) as sp:
        sp.step(t("xui.step.check_os"))
        _ensure_linux()

        sp.step(t("xui.step.install_deps"))
        _install_base_packages()

        sp.step(t("xui.step.install_xui"))
        if not command_exists(XUI_COMMAND):
            install_xui_script()

        sp.step(t("xui.step.generate_credentials"))
        private_key, public_key = generate_reality_keypair()
        if not private_key or not public_key:
            raise InstallError(t("xui.error.reality_key_failed"))
        uuid = gen_uuid()
        short_id = gen_short_id()
        trojan_password = gen_password()

        sp.step(t("xui.step.configure_panel"))
        if not configure_panel_settings(
            port=panel_port,
            username=panel_user,
            password=panel_password,
            base_path=panel_base_path,
        ):
            raise InstallError(t("xui.error.panel_config_failed"))
        try:
            restart_service()
        except Exception:
            pass
        vless = vless_reality_xhttp_inbound(
            port=vless_port,
            uuid=uuid,
            private_key=private_key,
            short_id=short_id,
            sni=sni,
            dest=dest,
            path=xhttp_path,
            remark=DEFAULT_VLESS_REMARK,
        )
        vless_link = build_vless_link(
            uuid=uuid,
            host=host,
            port=vless_port,
            public_key=public_key,
            sni=sni,
            short_id=short_id,
            path=xhttp_path,
            remark=DEFAULT_VLESS_REMARK,
        )
        inbounds = [vless]

        sp.step(t("xui.step.create_vless_xhttp"))
        trojan_link = ""
        if enable_trojan:
            sp.step(t("xui.step.create_trojan"))
            certificate_file, key_file = ensure_trojan_certificate(sni)
            trojan = trojan_inbound(
                port=trojan_port,
                password=trojan_password,
                sni=sni,
                remark=DEFAULT_TROJAN_REMARK,
                certificate_file=certificate_file,
                key_file=key_file,
            )
            inbounds.append(trojan)
            trojan_link = build_trojan_link(
                password=trojan_password,
                host=host,
                port=trojan_port,
                sni=sni,
                remark=DEFAULT_TROJAN_REMARK,
                allow_insecure=True,
            )

        api_ok = _create_inbounds(
            panel_port=panel_port,
            panel_user=panel_user,
            panel_password=panel_password,
            panel_base_path=panel_base_path,
            inbounds=inbounds,
        )

        sp.step(t("xui.step.start_service"))
        try:
            restart_service()
        except Exception:
            pass

        sp.step(t("xui.step.verify"))
        state: dict[str, object] = {
            "status": XUI_VERSION_LATEST,
            "panel_port": panel_port,
            "panel_user": panel_user,
            "panel_password": panel_password,
            "panel_base_path": panel_base_path,
            "api_configured": api_ok,
            "vless": {
                "host": host,
                "port": vless_port,
                "local_port": vless_port,
                "uuid": uuid,
                "public_key": public_key,
                "private_key": private_key,
                "short_id": short_id,
                "sni": sni,
                "dest": dest,
                "path": xhttp_path,
                "link": vless_link,
            },
        }
        if enable_trojan:
            state["trojan"] = {
                "host": host,
                "port": trojan_port,
                "local_port": trojan_port,
                "password": trojan_password,
                "sni": sni,
                "link": trojan_link,
            }
        write_secret_json(XUI_STATE_FILE, state)

        sp.step(t("xui.step.print_links"))

    print_success(t("xui.output.install_done"))
    if not api_ok:
        print_warning(t("xui.output.pending_inbounds", path=str(XUI_PENDING_INBOUNDS_FILE)))
    panel_url = HTTP_URL_TEMPLATE.format(host=LOOPBACK_HOST, port=panel_port, base_path=panel_base_path)
    console.print(f"{t('xui.output.panel')}: {panel_url}")
    console.print(f"{t('xui.output.panel_user')}: {panel_user}")
    console.print(f"{t('xui.output.panel_password')}: {panel_password}")
    console.print(f"{t('xui.output.vless_link')}: {vless_link}")
    if trojan_link:
        console.print(f"{t('xui.output.trojan_link')}: {trojan_link}")
    pause()


def uninstall_server() -> None:
    try:
        stop_and_disable_service()
        if XUI_STATE_FILE.exists():
            XUI_STATE_FILE.unlink()
    except Exception as exc:
        raise UninstallError(str(exc)) from exc
    print_success(t("xui.output.uninstall_done"))
    pause()


def diagnose_server() -> None:
    state = load_state()
    redacted = redact_state(state)
    print_info(t("xui.diagnose.title"))
    console.print(f"{t('xui.diagnose.service')}: {is_service_active()}")
    panel_port = state.get("panel_port")
    if isinstance(panel_port, int):
        console.print(f"{t('xui.diagnose.panel_port')}: {is_port_listening(panel_port)}")
    vless_state = state.get("vless")
    if isinstance(vless_state, dict):
        port = vless_state.get("local_port", vless_state.get("port"))
        if isinstance(port, int):
            console.print(f"{t('xui.diagnose.vless_port')}: {is_port_listening(port)}")
    trojan_state = state.get("trojan")
    if isinstance(trojan_state, dict):
        port = trojan_state.get("local_port", trojan_state.get("port"))
        if isinstance(port, int):
            console.print(f"{t('xui.diagnose.trojan_port')}: {is_port_listening(port)}")
    console.print(json.dumps(redacted, indent=2, ensure_ascii=False))
    pause()


def _print_links(state: dict[str, object]) -> None:
    vless_state = state.get("vless")
    if isinstance(vless_state, dict):
        link = vless_state.get("link")
        if isinstance(link, str):
            console.print(f"{t('xui.output.vless_link')}: {link}")
    trojan_state = state.get("trojan")
    if isinstance(trojan_state, dict):
        link = trojan_state.get("link")
        if isinstance(link, str):
            console.print(f"{t('xui.output.trojan_link')}: {link}")


def manage_nodes() -> None:
    state = load_state()
    if not state:
        print_warning(t("xui.manage.no_state"))
        pause()
        return
    breadcrumb = ["OpsKit", t("menu.software"), t("software.xui"), t("software.manage")]
    while True:
        try:
            key = select(
                breadcrumb=breadcrumb,
                subtitle=t("prompt.select"),
                choices=[
                    {"key": "1", "label": t("xui.manage.print_links")},
                    {"key": "2", "label": t("xui.manage.show_state")},
                    {"key": "3", "label": t("xui.manage.restart")},
                    {"key": "4", "label": t("xui.manage.logs")},
                ],
                theme_key="software",
            )
        except UserCancel:
            return
        if key is None:
            return
        if key == "1":
            _print_links(state)
            pause()
        elif key == "2":
            console.print(json.dumps(redact_state(state), indent=2, ensure_ascii=False))
            pause()
        elif key == "3":
            restart_service()
            print_success(t("xui.manage.restart_done"))
            pause()
        elif key == "4":
            subprocess.run([JOURNALCTL_COMMAND, "-u", XUI_SERVICE, "-n", XUI_LOG_LINES, JOURNAL_NO_PAGER_ARG], check=False)
            pause()
