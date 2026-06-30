"""x-ui 服务端安装、诊断与管理。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from rich.console import Console

from core.i18n import t
from core.progress import MultiStepProgress
from core.prompt import UserCancel, clear_screen, confirm, pause, select, text_input
from core.theme import get_icon, print_action_title, print_error, print_success, print_warning
from software.base import InstallError, UninstallError
from xui.constants import (
    APT_ASSUME_YES_ARG,
    APT_INSTALL_COMMAND,
    APT_UPDATE_COMMAND,
    BBR_KERNEL_MODULE,
    BBR_SYSCTL_FILE,
    BBR_SYSCTL_FILE_CONTENT,
    BBR_SYSPARAMS,
    MODPROBE_COMMAND,
    SYSCTL_COMMAND,
    SYSCTL_WRITE_ARG,
    SYSTEMCTL_COMMAND,
    XUI_SERVER_RECIPE_KEY,
    DEBIAN_FRONTEND_ENV,
    DEBIAN_FRONTEND_NONINTERACTIVE,
    DEFAULT_FINGERPRINT,
    DEFAULT_PANEL_BASE_PATH,
    DEFAULT_PANEL_PORT,
    DEFAULT_PANEL_USER,
    DEFAULT_REALITY_SNI,
    DEFAULT_VLESS_PORT,
    DEFAULT_VLESS_REMARK,
    APT_GET_COMMAND,
    CURL_COMMAND,
    HTTP_URL_TEMPLATE,
    JOURNALCTL_COMMAND,
    JOURNAL_NO_PAGER_ARG,
    LINUX_PLATFORMS,
    LOOPBACK_HOST,
    TRAFFIC_PERIOD_MONTH,
    TRAFFIC_PERIOD_TODAY,
    TRAFFIC_PERIOD_WEEK,
    SQLITE3_COMMAND,
    SQLITE_YUM_PACKAGE,
    YUM_COMMAND,
    YUM_INSTALL_COMMAND,
    XUI_COMMAND,
    XUI_DATABASE_FILE,
    XUI_LOG_LINES,
    XUI_PENDING_INBOUNDS_FILE,
    XUI_STATE_FILE,
    XUI_SERVICE,
    XUI_VERSION_LATEST,
    WSL_CONF_FILE,
    WSL_BOOT_HEADER,
    WSL_SYSTEMD_LINE,
    WSL_SYSTEMD_LINE_PATTERN,
    WSL_EXE_COMMAND,
    WSL_SHUTDOWN_ARG,
)
from xui.links import build_vless_link
from xui.templates import to_xui_api_payload, vless_reality_tcp_inbound
from xui.utils import (
    add_inbound,
    command_exists,
    configure_panel_settings,
    detect_public_host,
    detect_xui_version,
    get_panel_settings,
    enable_inbound_clients,
    generate_reality_keypair,
    gen_password,
    gen_short_id,
    gen_uuid,
    is_port_listening,
    is_service_active,
    load_state,
    redact_state,
    remove_xui_artifacts,
    restart_service,
    enable_service,
    install_traffic_timer,
    uninstall_traffic_timer,
    stop_and_disable_service,
    systemd_available,
    is_wsl,
    write_secret_json,
    install_xui_script,
)
from xui.traffic import compute_stats, human_bytes

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


def _enable_bbr() -> None:
    subprocess.run(
        [MODPROBE_COMMAND, BBR_KERNEL_MODULE],
        check=False, capture_output=True, text=True,
    )
    for param, value in BBR_SYSPARAMS.items():
        subprocess.run(
            [SYSCTL_COMMAND, SYSCTL_WRITE_ARG, f"{param}={value}"],
            check=False, capture_output=True, text=True,
        )
    BBR_SYSCTL_FILE.parent.mkdir(parents=True, exist_ok=True)
    BBR_SYSCTL_FILE.write_text(BBR_SYSCTL_FILE_CONTENT, encoding="utf-8")


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


def _enable_wsl_systemd() -> None:
    text = WSL_CONF_FILE.read_text(encoding="utf-8") if WSL_CONF_FILE.exists() else ""
    if re.search(WSL_SYSTEMD_LINE_PATTERN, text, flags=re.MULTILINE):
        new_text = re.sub(WSL_SYSTEMD_LINE_PATTERN, WSL_SYSTEMD_LINE, text, flags=re.MULTILINE)
    elif WSL_BOOT_HEADER in text:
        new_text = text.replace(WSL_BOOT_HEADER, f"{WSL_BOOT_HEADER}\n{WSL_SYSTEMD_LINE}", 1)
    else:
        sep = "" if text == "" or text.endswith("\n") else "\n"
        new_text = f"{text}{sep}{WSL_BOOT_HEADER}\n{WSL_SYSTEMD_LINE}\n"
    WSL_CONF_FILE.parent.mkdir(parents=True, exist_ok=True)
    WSL_CONF_FILE.write_text(new_text, encoding="utf-8")


def _restart_wsl() -> bool:
    """通过 WSL interop 调用 wsl.exe --shutdown 重启。返回是否成功发起。"""
    if not shutil.which(WSL_EXE_COMMAND):
        return False
    subprocess.run(
        [WSL_EXE_COMMAND, WSL_SHUTDOWN_ARG],
        check=False, capture_output=True, text=True,
    )
    return True


def _precheck_systemd(breadcrumb: list[str]) -> bool:
    """安装前提前判断 systemd 是否可用。返回 False 表示中止安装。

    仅在确实为 WSL 时才提供自动写入 /etc/wsl.conf + 重启 WSL；其它无 systemd
    环境（容器、SysV 等）只提示并询问是否继续，不触碰 wsl.conf 或 wsl.exe。
    """
    if systemd_available():
        return True
    print_warning(t("xui.output.no_systemd"))
    if not is_wsl():
        return confirm(breadcrumb=breadcrumb, prompt=t("xui.wsl.continue_confirm"))
    if confirm(breadcrumb=breadcrumb, prompt=t("xui.wsl.enable_confirm")):
        _enable_wsl_systemd()
        if shutil.which(WSL_EXE_COMMAND):
            print_success(t("xui.wsl.restarting"))
            _restart_wsl()
        else:
            print_success(t("xui.wsl.enabled"))
            pause()
        return False
    return confirm(breadcrumb=breadcrumb, prompt=t("xui.wsl.continue_confirm"))


def install_server() -> None:
    breadcrumb = ["OpsKit", t("menu.software"), t("software.xui"), t("software.install")]
    panel_port = _read_int(breadcrumb, "xui.input.panel_port", DEFAULT_PANEL_PORT)
    panel_user = _read_text(breadcrumb, "xui.input.panel_user", DEFAULT_PANEL_USER)
    panel_password = _read_text(breadcrumb, "xui.input.panel_password", gen_password())
    panel_base_path = _read_text(breadcrumb, "xui.input.panel_base_path", DEFAULT_PANEL_BASE_PATH)
    host = _read_text(breadcrumb, "xui.input.host", detect_public_host())
    vless_port = _read_int(breadcrumb, "xui.input.vless_port", DEFAULT_VLESS_PORT)
    sni = _read_text(breadcrumb, "xui.input.sni", DEFAULT_REALITY_SNI)
    dest = _read_text(breadcrumb, "xui.input.dest", f"{sni}:{DEFAULT_VLESS_PORT}")

    if not confirm(breadcrumb=breadcrumb, prompt=t("xui.confirm.install")):
        return

    if not _precheck_systemd(breadcrumb):
        return

    step_keys = [
        "xui.step.check_os",
        "xui.step.install_deps",
        "xui.step.enable_bbr",
        "xui.step.install_xui",
        "xui.step.generate_credentials",
        "xui.step.configure_panel",
        "xui.step.create_vless_tcp",
        "xui.step.start_service",
        "xui.step.verify",
        "xui.step.print_links",
    ]
    step_descs = [t(key) for key in step_keys]

    clear_screen()
    print_action_title(breadcrumb)
    with MultiStepProgress(step_descs) as sp:
        sp.step(t("xui.step.check_os"))
        _ensure_linux()
        from core.sysconfig import SysConfigManager
        SysConfigManager.save(XUI_SERVER_RECIPE_KEY, sysparams=BBR_SYSPARAMS)

        sp.step(t("xui.step.install_deps"))
        _install_base_packages()

        sp.step(t("xui.step.enable_bbr"))
        _enable_bbr()

        sp.step(t("xui.step.install_xui"))
        if not command_exists(XUI_COMMAND):
            install_xui_script()

        sp.step(t("xui.step.generate_credentials"))
        private_key, public_key = generate_reality_keypair()
        if not private_key or not public_key:
            raise InstallError(t("xui.error.reality_key_failed"))
        uuid = gen_uuid()
        short_id = gen_short_id()

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
        effective = get_panel_settings()
        if isinstance(effective.get("port"), int):
            panel_port = int(effective["port"])
        if isinstance(effective.get("base_path"), str):
            panel_base_path = str(effective["base_path"])
        vless = vless_reality_tcp_inbound(
            port=vless_port,
            uuid=uuid,
            private_key=private_key,
            short_id=short_id,
            sni=sni,
            dest=dest,
            remark=DEFAULT_VLESS_REMARK,
        )
        vless_link = build_vless_link(
            uuid=uuid,
            host=host,
            port=vless_port,
            public_key=public_key,
            sni=sni,
            short_id=short_id,
            remark=DEFAULT_VLESS_REMARK,
        )
        inbounds = [vless]

        sp.step(t("xui.step.create_vless_tcp"))

        api_ok = _create_inbounds(
            panel_port=panel_port,
            panel_user=panel_user,
            panel_password=panel_password,
            panel_base_path=panel_base_path,
            inbounds=inbounds,
        )

        sp.step(t("xui.step.start_service"))
        try:
            enable_service()
            restart_service()
        except Exception:
            pass
        install_traffic_timer()

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
                "link": vless_link,
            },
        }
        write_secret_json(XUI_STATE_FILE, state)
        SysConfigManager.mark_installed(XUI_SERVER_RECIPE_KEY)

        sp.step(t("xui.step.print_links"))

    print_success(t("xui.output.install_done"))
    if not systemd_available():
        print_warning(t("xui.output.no_systemd"))
    if not api_ok:
        print_warning(t("xui.output.pending_inbounds", path=str(XUI_PENDING_INBOUNDS_FILE)))
    panel_url = HTTP_URL_TEMPLATE.format(host=host or LOOPBACK_HOST, port=panel_port, base_path=panel_base_path)
    console.print(f"{t('xui.output.panel')}: {panel_url}")
    console.print(f"{t('xui.output.panel_user')}: {panel_user}")
    console.print(f"{t('xui.output.panel_password')}: {panel_password}")
    console.print(f"{t('xui.output.vless_link')}: {vless_link}")
    pause()


def uninstall_server() -> None:
    from core.progress import MultiStepProgress

    print_action_title(["OpsKit", t("menu.software"), t("software.xui"), t("software.uninstall")])
    descs = [
        t("software.step.stop_service"),
        t("software.step.remove_files"),
        t("software.step.cleanup"),
    ]
    try:
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            stop_and_disable_service()

            sp.step(descs[1])
            uninstall_traffic_timer()
            remove_xui_artifacts()
            BBR_SYSCTL_FILE.unlink(missing_ok=True)

            sp.step(descs[2])
            from core.sysconfig import SysConfigManager
            SysConfigManager.restore(XUI_SERVER_RECIPE_KEY)
            SysConfigManager.remove(XUI_SERVER_RECIPE_KEY)
            sp.complete()
    except Exception as exc:
        raise UninstallError(str(exc)) from exc
    print_success(t("xui.output.uninstall_done"))


def diagnose_server() -> None:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    state = load_state()
    breadcrumb = ["OpsKit", t("menu.software"), t("software.xui"), t("software.diagnose")]
    clear_screen()
    print_action_title(breadcrumb)

    dk = "xui.diagnose"
    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"
    _SEC = "bold #89b4fa"
    _OK = "#a6e3a1"
    _BAD = "#f38ba8"

    def _lbl(s: str) -> Text:
        return Text(s, style=_LABEL)

    def _val(s: object) -> Text:
        return Text(str(s), style=_VALUE)

    def _sec(s: str) -> Text:
        return Text(f"── {s} ──", style=_SEC)

    def _dot(ok: bool) -> Text:
        key = f"{dk}.active" if ok else f"{dk}.inactive"
        return Text(f"● {t(key)}", style=_OK if ok else _BAD)

    def _yesno(ok: bool, yes_key: str, no_key: str) -> Text:
        return Text(t(yes_key) if ok else t(no_key), style=f"bold {_OK if ok else _BAD}")

    def _port_cell(port: object) -> Text:
        if not isinstance(port, int):
            return Text("—", style=_LABEL)
        ok = is_port_listening(port)
        label = t(f"{dk}.port_listening") if ok else t(f"{dk}.port_not_listening")
        return Text(f"{port:<5}  {label}", style=f"bold {_OK if ok else _BAD}")

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    # ── 1. 服务状态 ──
    tbl.add_row(_sec(t(f"{dk}.section_service")), Text(""))
    tbl.add_row(_lbl(t(f"{dk}.service")), _dot(is_service_active()))
    enabled = False
    try:
        r = subprocess.run(
            [SYSTEMCTL_COMMAND, "is-enabled", XUI_SERVICE],
            capture_output=True, text=True, check=False,
        )
        enabled = r.stdout.strip() == "enabled"
    except Exception:
        pass
    tbl.add_row(_lbl(t(f"{dk}.autostart")), _yesno(enabled, f"{dk}.enabled", f"{dk}.disabled"))
    tbl.add_row(_lbl(t(f"{dk}.command")), _yesno(command_exists(XUI_COMMAND), f"{dk}.available", f"{dk}.missing"))
    tbl.add_row(_lbl(t(f"{dk}.version")), _val(detect_xui_version() or "—"))
    tbl.add_row(_lbl(t(f"{dk}.database")), _yesno(XUI_DATABASE_FILE.exists(), f"{dk}.exists", f"{dk}.missing"))
    tbl.add_row(Text(""), Text(""))

    # ── 2. 端口与网络 ──
    tbl.add_row(_sec(t(f"{dk}.section_network")), Text(""))
    panel_port = state.get("panel_port")
    tbl.add_row(_lbl(t(f"{dk}.panel_port")), _port_cell(panel_port))
    vless_state = state.get("vless") if isinstance(state.get("vless"), dict) else {}
    vless_port = vless_state.get("local_port", vless_state.get("port"))
    tbl.add_row(_lbl(t(f"{dk}.vless_port")), _port_cell(vless_port))
    if isinstance(panel_port, int):
        host = vless_state.get("host") or LOOPBACK_HOST
        base_path = state.get("panel_base_path") or ""
        url = HTTP_URL_TEMPLATE.format(host=host, port=panel_port, base_path=base_path)
        tbl.add_row(_lbl(t(f"{dk}.panel_url")), _val(url))
    tbl.add_row(Text(""), Text(""))

    # ── 3. 节点参数（非敏感）──
    if vless_state:
        tbl.add_row(_sec(t(f"{dk}.section_node")), Text(""))
        tbl.add_row(_lbl(t(f"{dk}.node_host")), _val(vless_state.get("host", "—")))
        tbl.add_row(_lbl(t(f"{dk}.node_sni")), _val(vless_state.get("sni", "—")))
        tbl.add_row(_lbl(t(f"{dk}.node_dest")), _val(vless_state.get("dest", "—")))
        tbl.add_row(_lbl(t(f"{dk}.node_security")), _val("reality"))
        tbl.add_row(_lbl(t(f"{dk}.node_fingerprint")), _val(DEFAULT_FINGERPRINT))
        tbl.add_row(
            _lbl(t(f"{dk}.api_configured")),
            _yesno(bool(state.get("api_configured")), f"{dk}.configured", f"{dk}.not_configured"),
        )

    console.print(Panel(
        tbl,
        title=f"[bold]{t(f'{dk}.title')}[/bold]",
        border_style="#89b4fa",
        padding=(1, 2),
    ))
    pause()


def _print_links(state: dict[str, object]) -> None:
    vless_state = state.get("vless")
    if isinstance(vless_state, dict):
        link = vless_state.get("link")
        if isinstance(link, str):
            console.print(f"{t('xui.output.vless_link')}: {link}")


def _show_traffic() -> None:
    stats = compute_stats()
    if not stats:
        print_warning(t("xui.manage.no_state"))
        return
    periods = [
        ("total", t("xui.traffic.total")),
        (TRAFFIC_PERIOD_TODAY, t("xui.traffic.today")),
        (TRAFFIC_PERIOD_WEEK, t("xui.traffic.week")),
        (TRAFFIC_PERIOD_MONTH, t("xui.traffic.month")),
    ]
    up_label = t("xui.traffic.up")
    down_label = t("xui.traffic.down")
    multi = len(stats) > 1
    for node in stats:
        if multi:
            console.print(str(node.get("remark") or ""))
        for period_key, period_label in periods:
            row = node.get(period_key) or {}
            up = human_bytes(row.get("up"))
            down = human_bytes(row.get("down"))
            console.print(f"  {period_label}  {up_label} {up}  {down_label} {down}")


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
                    {"key": "1", "label": f"{get_icon('link')} {t('xui.manage.print_links')}"},
                    {"key": "2", "label": f"{get_icon('state')} {t('xui.manage.show_state')}"},
                    {"key": "3", "label": f"{get_icon('logs')} {t('xui.manage.logs')}"},
                    {"key": "4", "label": f"{get_icon('traffic')} {t('xui.manage.traffic')}"},
                ],
                theme_key="software",
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if key is None:
            return
        clear_screen()
        if key == "1":
            print_action_title(breadcrumb)
            _print_links(state)
            pause()
        elif key == "2":
            print_action_title(breadcrumb)
            console.print(json.dumps(redact_state(state), indent=2, ensure_ascii=False))
            pause()
        elif key == "3":
            print_action_title(breadcrumb)
            subprocess.run([JOURNALCTL_COMMAND, "-u", XUI_SERVICE, "-n", XUI_LOG_LINES, JOURNAL_NO_PAGER_ARG], check=False)
            pause()
        elif key == "4":
            print_action_title(breadcrumb)
            _show_traffic()
            pause()
