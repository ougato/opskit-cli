"""WireGuard 私网客户端安装 / 卸载 / 诊断逻辑"""
from __future__ import annotations

from software.base import InstallError, UninstallError
from core.theme import console


def _save_client_state(data: dict) -> None:
    """保存客户端 state 文件"""
    import json
    from wireguard.constants import WG_CLIENT_STATE_FILE
    from wireguard.utils import write_file
    write_file(WG_CLIENT_STATE_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def _load_client_state() -> dict:
    """加载客户端 state 文件"""
    import json
    from pathlib import Path
    from wireguard.constants import WG_CLIENT_STATE_FILE
    p = Path(WG_CLIENT_STATE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    return {}


def install_client() -> None:
    """私网客户端安装向导"""
    from core.i18n import t

    breadcrumb = ["OpsKit", t("menu.software"), "WireGuard", t("software.wg_client")]
    _install_client_token(breadcrumb)


def _install_client_token(breadcrumb: list[str]) -> None:
    """令牌模式安装客户端"""
    from core.i18n import t
    from core.prompt import text_input, pause, clear_screen, UserCancel
    from core.theme import print_error, print_action_title
    from core.progress import MultiStepProgress
    from wireguard.constants import (
        WG_UDP_PORT, VPN_SERVER_IP, CLIENT_XRAY_LOCAL_PORT,
        WG_CONFIG_FILE, XRAY_CONFIG_FILE, WG_SERVICE, XRAY_SERVICE,
        WG_CLIENT_MTU, WG_KEEPALIVE,
    )
    from wireguard.utils import (
        detect_default_iface, enable_and_start, stop_and_disable, write_file,
        get_os_id, install_wireguard_pkg, install_xray,
    )
    from wireguard.templates import xray_client_config, wg_client_config
    from wireguard.token import decode_token

    # ── 粘贴令牌 ─────────────────────────────────────────────────────────────
    try:
        raw_token = text_input(
            breadcrumb=breadcrumb,
            prompt=t("wireguard.input_token"),
            theme_key="software",
        )
    except UserCancel:
        return
    if not raw_token or not raw_token.strip():
        print_error(t("wireguard.token_required"))
        pause()
        return

    try:
        data = decode_token(raw_token.strip())
    except ValueError as e:
        print_error(str(e))
        pause()
        return

    server_ip = data["server_ip"]
    server_port = data["server_port"]
    wg_server_pub = data["wg_server_pubkey"]
    wg_client_priv = data["wg_client_privkey"]
    wg_psk = data["wg_psk"]
    client_ip = data["client_ip"]
    reality_pub = data["reality_pubkey"]
    uuid = data["uuid"]
    short_id = data["short_id"]
    sni = data["sni"]

    wg_port = WG_UDP_PORT
    local_port = CLIENT_XRAY_LOCAL_PORT
    iface = detect_default_iface()

    # ── 端口预检（用域名，信息展示，不阻断安装）────────────────────────
    import socket as _socket
    _check_host = sni if sni else server_ip
    _port_ok = False
    try:
        with _socket.create_connection((_check_host, server_port), timeout=5):
            _port_ok = True
    except Exception:
        pass

    from core.theme import console as _pre_con, print_warning
    if _port_ok:
        _pre_con.print(f"[#a6e3a1]{t('wireguard.check_port_ok').format(port=server_port)}[/#a6e3a1]")
    else:
        print_warning(t("wireguard.check_port_fail").format(ip=_check_host, port=server_port))

    # ── 安装步骤 ─────────────────────────────────────────────────────────────
    step_keys = [
        "wireguard.step.check_os",
        "wireguard.step.install_wg",
        "wireguard.step.install_xray",
        "wireguard.step.write_xray_config",
        "wireguard.step.write_wg_config",
        "wireguard.step.start_services",
        "wireguard.step.verify",
    ]

    clear_screen()
    print_action_title(breadcrumb)

    with MultiStepProgress([t(s) for s in step_keys]) as sp:
        import subprocess

        sp.step(t("wireguard.step.check_os"))
        os_id = get_os_id()
        if os_id not in ("debian", "ubuntu", "centos", "rocky", "almalinux", "rhel", "fedora"):
            raise InstallError(t("wireguard.error.unsupported_os", os_id=os_id))

        from core.sysconfig import SysConfigManager as _SCM
        _SCM.save("wg_client", sysparams=None, pre_install={})

        sp.step(t("wireguard.step.install_wg"))
        install_wireguard_pkg(os_id)

        sp.step(t("wireguard.step.install_xray"))
        install_xray()

        sp.step(t("wireguard.step.write_xray_config"))
        xray_cfg = xray_client_config(
            sni=sni,
            server_port=server_port,
            uuid=uuid,
            local_port=local_port,
            wg_port=wg_port,
        )
        write_file(XRAY_CONFIG_FILE, xray_cfg)

        sp.step(t("wireguard.step.write_wg_config"))
        wg_cfg = wg_client_config(
            client_private_key=wg_client_priv,
            client_ip=client_ip,
            server_public_key=wg_server_pub,
            psk=wg_psk,
            server_endpoint=f"127.0.0.1:{local_port}",
            mtu=WG_CLIENT_MTU,
            keepalive=WG_KEEPALIVE,
        )
        write_file(WG_CONFIG_FILE, wg_cfg)

        sp.step(t("wireguard.step.start_services"))
        from pathlib import Path as _Path
        _dropin_dir = _Path(f"/etc/systemd/system/{WG_SERVICE}.service.d")
        _dropin_dir.mkdir(parents=True, exist_ok=True)
        (_dropin_dir / "after-xray.conf").write_text(
            "[Unit]\nAfter=xray.service\nWants=xray.service\n"
        )
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        subprocess.run(["wg-quick", "down", "wg0"], check=False, capture_output=True)
        stop_and_disable(WG_SERVICE)
        stop_and_disable(XRAY_SERVICE)
        enable_and_start(XRAY_SERVICE)
        enable_and_start(WG_SERVICE)
        _SCM.mark_installed("wg_client")

        sp.step(t("wireguard.step.verify"))
        import time
        time.sleep(2)
        from wireguard.utils import is_service_active
        _xray_ok = is_service_active(XRAY_SERVICE)
        _wg_ok   = is_service_active(WG_SERVICE)
        ping_ok = False
        try:
            r = subprocess.run(
                ["ping", "-c", "2", "-W", "2", VPN_SERVER_IP],
                capture_output=True, text=True, check=False,
            )
            ping_ok = (r.returncode == 0)
        except Exception:
            pass
        if not (_xray_ok and _wg_ok):
            from core.theme import print_warning as _pw
            _pw(f"xray={'active' if _xray_ok else 'INACTIVE'} wg={'active' if _wg_ok else 'INACTIVE'}")

    # ── 保存客户端 state ──────────────────────────────────────────────────────
    _save_client_state({
        "token": raw_token.strip(),
        "client_ip": client_ip,
        "server_ip": server_ip,
        "server_port": server_port,
        "sni": sni,
        "uuid": uuid,
        "short_id": short_id,
        "wg_server_pub": wg_server_pub,
    })

    # ── 安装成功 Panel ────────────────────────────────────────────────────────
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text as _Text
    from core.prompt import pause
    clear_screen()
    print_action_title(breadcrumb)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    _ping_text = _Text(VPN_SERVER_IP, style="#a6e3a1" if ping_ok else "#f38ba8")
    rows = [
        (t("wireguard.client_diagnose.client_ip"), client_ip),
        (t("wireguard.info.vpn_gateway"),          VPN_SERVER_IP),
        (t("wireguard.info.xray_port"),            str(server_port)),
        (t("wireguard.info.domain"),               sni),
    ]
    for label, value in rows:
        tbl.add_row(_Text(label, style="#7f849c"), _Text(value, style="bold #cdd6f4"))
    tbl.add_row(_Text(t("wireguard.client_diagnose.ping_gateway"), style="#7f849c"), _ping_text)
    console.print(Panel(
        tbl,
        title=f"[bold #a6e3a1]{t('wireguard.install_client_success')}[/bold #a6e3a1]",
        border_style="#a6e3a1",
        padding=(1, 2),
    ))
    pause()


def uninstall_client() -> None:
    """私网客户端卸载"""
    from core.i18n import t
    from core.theme import print_success, print_action_title
    from core.progress import MultiStepProgress
    from wireguard.constants import (
        WG_CONFIG_DIR, XRAY_CONFIG_DIR, WG_SERVICE, XRAY_SERVICE,
    )
    from wireguard.utils import stop_and_disable

    print_action_title(["OpsKit", t("menu.software"), "WireGuard", t("software.wg_client_uninstall")])

    step_keys = [
        "wireguard.step.client_uninstall_stop_wg",
        "wireguard.step.client_uninstall_stop_xray",
        "wireguard.step.client_uninstall_clean_config",
        "wireguard.step.client_uninstall_clean_routes",
    ]
    descs = [t(k) for k in step_keys]

    import shutil
    import subprocess

    with MultiStepProgress(descs) as sp:
        sp.step(descs[0])
        subprocess.run(["wg-quick", "down", "wg0"],
                       check=False, capture_output=True, text=True)
        stop_and_disable(WG_SERVICE)

        sp.step(descs[1])
        stop_and_disable(XRAY_SERVICE)

        sp.step(descs[2])
        from pathlib import Path as _Path
        from wireguard.constants import XRAY_BINARY
        for path in (WG_CONFIG_DIR, XRAY_CONFIG_DIR):
            shutil.rmtree(path, ignore_errors=True)
        _Path("/etc/sysctl.d/99-wg.conf").unlink(missing_ok=True)
        from core.sysconfig import SysConfigManager as _SCM
        _SCM.restore("wg_client")
        _SCM.remove("wg_client")
        for svc in (XRAY_SERVICE,):
            _Path(f"/etc/systemd/system/{svc}.service").unlink(missing_ok=True)
            shutil.rmtree(f"/etc/systemd/system/{svc}.service.d", ignore_errors=True)
        for tpl in ("xray@",):
            _Path(f"/etc/systemd/system/{tpl}.service").unlink(missing_ok=True)
            shutil.rmtree(f"/etc/systemd/system/{tpl}.service.d", ignore_errors=True)
        shutil.rmtree(f"/etc/systemd/system/{WG_SERVICE}.service.d", ignore_errors=True)
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        _Path(XRAY_BINARY).unlink(missing_ok=True)
        from core.paths import xray_log_dir, xray_data_dir
        for path in (xray_log_dir(), xray_data_dir()):
            shutil.rmtree(str(path), ignore_errors=True)

        sp.step(descs[3])
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                capture_output=True, text=True, check=False,
            )
            iface = detect_default_iface()
            conn_name = iface
            for line in r.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[1].strip() == iface:
                    conn_name = parts[0].strip()
                    break
            subprocess.run(
                ["nmcli", "connection", "modify", conn_name,
                 "-ipv4.routes", f"0.0.0.0/0"],
                check=False, capture_output=True, text=True,
            )
            subprocess.run(["nmcli", "connection", "reload"],
                           check=False, capture_output=True, text=True)
        except Exception:
            pass

    console.print()
    print_success(t("wireguard.client_diagnose.uninstall_success"))


def diagnose_client() -> None:
    """私网客户端诊断"""
    import json
    import subprocess
    from pathlib import Path
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from core.i18n import t
    from core.theme import print_action_title
    from core.prompt import pause
    from wireguard.constants import (
        WG_SERVICE, XRAY_SERVICE, WG_CONFIG_FILE, XRAY_CONFIG_FILE, VPN_SERVER_IP,
    )
    from wireguard.utils import is_service_active

    print_action_title(["OpsKit", t("menu.software"), "WireGuard", t("software.diagnose")])

    dk = "wireguard.client_diagnose"

    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"
    _SEC   = "bold #89b4fa"

    def _lbl(s: str) -> Text:
        return Text(s, style=_LABEL)

    def _val(s: str) -> Text:
        return Text(s, style=_VALUE)

    def _sec(s: str) -> Text:
        return Text(f"── {s} ──", style=_SEC)

    def _dot(ok: bool) -> Text:
        return Text("● active", style="#a6e3a1") if ok else Text("● inactive", style="#f38ba8")

    def _trunc(s: str, n: int = 42) -> str:
        return s if len(s) <= n else s[:n] + "..."

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    # ── 1. 服务状态 ────────────────────────────────────────────────────────────
    tbl.add_row(_sec(t(f"{dk}.section_services")), Text(""))
    wg_ok   = is_service_active(WG_SERVICE)
    xray_ok = is_service_active(XRAY_SERVICE)
    tbl.add_row(_lbl(t(f"{dk}.wg_service")),   _dot(wg_ok))
    tbl.add_row(_lbl(t(f"{dk}.xray_service")), _dot(xray_ok))
    tbl.add_row(Text(""), Text(""))

    # ── 2. 连接参数（从配置文件读取）─────────────────────────────────────────
    tbl.add_row(_sec(t(f"{dk}.section_config")), Text(""))
    wg_pub     = "—"
    client_ip  = "—"
    server_ip  = "—"
    xray_port  = "—"
    wg_port    = "—"
    try:
        r = subprocess.run(["wg", "show", "wg0", "public-key"],
                           capture_output=True, text=True, check=False)
        if r.returncode == 0 and r.stdout.strip():
            wg_pub = r.stdout.strip()
    except Exception:
        pass
    try:
        wg_conf = Path(WG_CONFIG_FILE).read_text()
        for line in wg_conf.splitlines():
            line = line.strip()
            if line.startswith("Address"):
                client_ip = line.split("=", 1)[1].strip() if "=" in line else "—"
            elif line.startswith("Endpoint"):
                ep = line.split("=", 1)[1].strip() if "=" in line else ""
                if ":" in ep:
                    server_ip = ep.rsplit(":", 1)[0].strip()
                    wg_port   = ep.rsplit(":", 1)[1].strip()
    except Exception:
        pass
    try:
        cfg = json.loads(Path(XRAY_CONFIG_FILE).read_text())
        outbounds = cfg.get("outbounds", [])
        for ob in outbounds:
            settings = ob.get("settings", {})
            vnext = settings.get("vnext", [])
            if vnext:
                xray_port = str(vnext[0].get("port", "—"))
                real_ip = vnext[0].get("address", "")
                if real_ip:
                    server_ip = real_ip
                break
    except Exception:
        pass
    _diag_tcp_ok = False
    try:
        _real_port = int(xray_port) if xray_port != "—" else 0
        _real_ip = server_ip if server_ip not in ("—", "127.0.0.1") else ""
        if _real_ip and _real_port:
            import socket as _socket_diag
            with _socket_diag.create_connection((_real_ip, _real_port), timeout=5):
                _diag_tcp_ok = True
    except Exception:
        pass
    _diag_conn_text = Text(
        f"\u25cf TCP {xray_port}",
        style="bold #a6e3a1" if _diag_tcp_ok else "bold #f38ba8",
    )
    tbl.add_row(_lbl(t(f"{dk}.wg_pub")),    _val(_trunc(wg_pub)))
    tbl.add_row(_lbl(t(f"{dk}.client_ip")), _val(client_ip))
    tbl.add_row(_lbl(t(f"{dk}.server_ip")), _val(server_ip))
    tbl.add_row(_lbl(t(f"{dk}.xray_port")), _val(xray_port))
    tbl.add_row(_lbl(t(f"{dk}.wg_port")),   _val(wg_port))
    tbl.add_row(_lbl(t("wireguard.client_connectivity")), _diag_conn_text)
    tbl.add_row(Text(""), Text(""))

    # ── 3. 连通性（ping 网关）──────────────────────────────────────────────
    tbl.add_row(_sec(t(f"{dk}.section_ping")), Text(""))
    try:
        ping_result = subprocess.run(
            ["ping", "-c", "2", "-W", "2", VPN_SERVER_IP],
            capture_output=True, text=True, check=False,
        )
        ping_ok = ping_result.returncode == 0
    except Exception:
        ping_ok = False
    ping_text = Text(VPN_SERVER_IP, style="#a6e3a1" if ping_ok else "#f38ba8")
    tbl.add_row(_lbl(t(f"{dk}.ping_gateway")), ping_text)

    console.print(Panel(
        tbl,
        title=f"[bold]{t(f'{dk}.title')}[/bold]",
        border_style="#89b4fa",
        padding=(1, 2),
    ))
    pause()


def manage_client() -> None:
    """客户端管理主菜单（查看 / 更新令牌）"""
    from core.i18n import t
    from core.prompt import select, UserCancel, pause
    from core.theme import get_icon, get_color, print_action_title, print_warning
    from core.sysconfig import _load as _sc_load

    mk = "wireguard.client_manage"
    breadcrumb = ["OpsKit", t("menu.software"), "WireGuard",
                  t("software.wg_client"), t(f"{mk}.title")]
    muted = get_color("muted")

    while True:
        entry = _sc_load().get("wg_client", {})
        installed = entry.get("status") == "installed"

        if installed:
            choices = [
                {"key": "1", "label": f"{get_icon('search')} {t(f'{mk}.view')}"},
                {"key": "2", "label": f"{get_icon('update_token')} {t(f'{mk}.update_token')}"},
            ]
        else:
            choices = [
                {"key": "1", "label": f"[{muted}]{get_icon('search')} {t(f'{mk}.view')}[/{muted}]", "disabled": True},
                {"key": "2", "label": f"[{muted}]{get_icon('update_token')} {t(f'{mk}.update_token')}[/{muted}]", "disabled": True},
            ]

        try:
            key = select(
                breadcrumb=breadcrumb,
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key="software",
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break

        if not installed:
            print_warning(t(f"{mk}.not_installed"))
            pause()
            continue

        if key == "1":
            view_client_info(breadcrumb)
        elif key == "2":
            update_client_token(breadcrumb)


def view_client_info(breadcrumb: list[str]) -> None:
    """查看客户端敏感连接信息"""
    from core.i18n import t
    from core.prompt import pause, clear_screen
    from core.theme import print_action_title, print_warning
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    mk = "wireguard.client_manage"
    sub = [*breadcrumb, t(f"{mk}.view")]

    state = _load_client_state()
    if not state:
        print_warning(t(f"{mk}.no_state"))
        pause()
        return

    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"
    _TOKEN = "#6c7086"

    def _trunc(s: str, n: int = 56) -> str:
        return s if len(s) <= n else s[:n] + "..."

    clear_screen()
    print_action_title(sub)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    rows = [
        (t(f"{mk}.field_ip"),     state.get("client_ip", "—")),
        (t(f"{mk}.field_server"), state.get("server_ip", "—")),
        (t(f"{mk}.field_port"),   str(state.get("server_port", "—"))),
        (t(f"{mk}.field_sni"),    state.get("sni", "—")),
        (t(f"{mk}.field_uuid"),   state.get("uuid", "—")),
    ]
    for label, value in rows:
        tbl.add_row(Text(label, style=_LABEL), Text(value, style=_VALUE))

    token = state.get("token", "")
    tbl.add_row(Text(""), Text(""))
    tbl.add_row(Text(t(f"{mk}.field_token"), style=_LABEL),
                Text(_trunc(token), style=_TOKEN))

    console.print(Panel(
        tbl,
        title=f"[bold #89b4fa]{t(f'{mk}.view_title')}[/bold #89b4fa]",
        border_style="#89b4fa",
        padding=(1, 2),
    ))
    pause()


def update_client_token(breadcrumb: list[str]) -> None:
    """更新令牌：解析新令牌，重写配置并重启服务"""
    import subprocess
    from core.i18n import t
    from core.prompt import pause, clear_screen, text_input, UserCancel
    from core.theme import print_error, print_action_title, print_success
    from core.progress import MultiStepProgress
    from wireguard.constants import (
        WG_UDP_PORT, CLIENT_XRAY_LOCAL_PORT,
        WG_CONFIG_FILE, XRAY_CONFIG_FILE, WG_SERVICE, XRAY_SERVICE,
        WG_CLIENT_MTU, WG_KEEPALIVE,
    )
    from wireguard.utils import (
        detect_default_iface, enable_and_start, stop_and_disable, write_file,
    )
    from wireguard.templates import xray_client_config, wg_client_config
    from wireguard.token import decode_token

    mk = "wireguard.client_manage"
    sub = [*breadcrumb, t(f"{mk}.update_token")]

    try:
        raw_token = text_input(
            breadcrumb=sub,
            prompt=t(f"{mk}.input_token"),
            theme_key="software",
        )
    except UserCancel:
        return
    if not raw_token or not raw_token.strip():
        print_error(t(f"{mk}.token_empty"))
        pause()
        return

    try:
        data = decode_token(raw_token.strip())
    except ValueError as e:
        print_error(t(f"{mk}.update_fail", detail=str(e)))
        pause()
        return

    server_ip    = data["server_ip"]
    server_port  = data["server_port"]
    wg_server_pub = data["wg_server_pubkey"]
    wg_client_priv = data["wg_client_privkey"]
    wg_psk       = data["wg_psk"]
    client_ip    = data["client_ip"]
    uuid         = data["uuid"]
    short_id     = data["short_id"]
    sni          = data["sni"]

    wg_port   = WG_UDP_PORT
    local_port = CLIENT_XRAY_LOCAL_PORT

    step_descs = [
        t("wireguard.step.write_xray_config"),
        t("wireguard.step.write_wg_config"),
        t("wireguard.step.start_services"),
        t("wireguard.step.verify"),
    ]

    clear_screen()
    print_action_title(sub)

    with MultiStepProgress(step_descs) as sp:
        sp.step(step_descs[0])
        xray_cfg = xray_client_config(
            sni=sni,
            server_port=server_port,
            uuid=uuid,
            local_port=local_port,
            wg_port=wg_port,
        )
        write_file(XRAY_CONFIG_FILE, xray_cfg)

        sp.step(step_descs[1])
        wg_cfg = wg_client_config(
            client_private_key=wg_client_priv,
            client_ip=client_ip,
            server_public_key=wg_server_pub,
            psk=wg_psk,
            server_endpoint=f"127.0.0.1:{local_port}",
            mtu=WG_CLIENT_MTU,
            keepalive=WG_KEEPALIVE,
        )
        write_file(WG_CONFIG_FILE, wg_cfg)

        sp.step(step_descs[2])
        subprocess.run(["wg-quick", "down", "wg0"], check=False, capture_output=True)
        stop_and_disable(WG_SERVICE)
        stop_and_disable(XRAY_SERVICE)
        enable_and_start(XRAY_SERVICE)
        enable_and_start(WG_SERVICE)

        sp.step(step_descs[3])
        import time
        time.sleep(2)
        from wireguard.utils import is_service_active
        _xray_ok = is_service_active(XRAY_SERVICE)
        _wg_ok   = is_service_active(WG_SERVICE)

    _save_client_state({
        "token": raw_token.strip(),
        "client_ip": client_ip,
        "server_ip": server_ip,
        "server_port": server_port,
        "sni": sni,
        "uuid": uuid,
        "short_id": short_id,
        "wg_server_pub": wg_server_pub,
    })

    clear_screen()
    print_action_title(sub)
    if _xray_ok and _wg_ok:
        print_success(t(f"{mk}.update_success"))
    else:
        from core.theme import print_warning
        print_warning(
            f"xray={'active' if _xray_ok else 'INACTIVE'} "
            f"wg={'active' if _wg_ok else 'INACTIVE'}"
        )
    pause()
