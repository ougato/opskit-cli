"""WireGuard 私网客户端安装 / 卸载 / 诊断逻辑"""
from __future__ import annotations

from software.base import InstallError, UninstallError
from core.theme import console


def _detect_local_dns(iface: str, vpn_dns: str | None = None) -> str | None:
    """检测本机首选 DNS（排除 VPN DNS 本身，不受 wg-quick bind-mount 污染影响）。
    优先 nmcli（读 NetworkManager 连接配置，不受 resolv.conf 覆盖影响），
    fallback 读 /etc/resolv.conf 第一条 nameserver，若与 vpn_dns 相同则认定已被污染，返回 None。
    """
    import subprocess
    import re

    _vpn = (vpn_dns or "").strip()

    # 1. nmcli 优先（不受 wg-quick bind-mount 影响）
    try:
        r = subprocess.run(
            ["nmcli", "dev", "show", iface],
            capture_output=True, text=True, check=False, timeout=5,
        )
        for line in r.stdout.splitlines():
            m = re.match(r"IP4\.DNS\[1\]:\s+(\S+)", line)
            if m:
                ip = m.group(1).strip()
                if ip and ip != _vpn:
                    return ip
    except Exception:
        pass

    # 2. resolv.conf fallback（排除已被 wg-quick 污染的情况）
    try:
        from pathlib import Path
        for line in Path("/etc/resolv.conf").read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith("nameserver"):
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[1].strip()
                    if ip and ip != _vpn:
                        return ip
    except Exception:
        pass

    return None


def _merge_dns(vpn_dns: str | None, local_dns: str | None) -> str | None:
    """合并本机 DNS 与 VPN DNS，确保无重复、本机优先。
    - vpn_dns=None（旧 token）→ 返回 None，不写 DNS 行（向后兼容）
    - local_dns 检测失败 → 只用 vpn_dns
    - 两者均有且不同 → '{local_dns}, {vpn_dns}'
    """
    if not vpn_dns:
        return None
    if local_dns and local_dns != vpn_dns:
        return f"{local_dns}, {vpn_dns}"
    return vpn_dns


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


def _alloc_local_port(tunnels: list[dict]) -> int:
    """从已安装隧道列表中自动分配下一个可用本地端口。
    在 CLIENT_XRAY_LOCAL_PORT_MIN~MAX 范围内找最小未占用端口。
    同时扫描系统实际监听端口，避免与旧隧道或其他进程冲突。
    """
    from wireguard.constants import CLIENT_XRAY_LOCAL_PORT_MIN, CLIENT_XRAY_LOCAL_PORT_MAX
    used: set[int] = {t.get("local_port") for t in tunnels if t.get("local_port")}
    used |= _scan_listening_ports(CLIENT_XRAY_LOCAL_PORT_MIN, CLIENT_XRAY_LOCAL_PORT_MAX)
    for p in range(CLIENT_XRAY_LOCAL_PORT_MIN, CLIENT_XRAY_LOCAL_PORT_MAX + 1):
        if p not in used:
            return p
    return CLIENT_XRAY_LOCAL_PORT_MIN


def _scan_listening_ports(port_min: int, port_max: int) -> set[int]:
    """扫描系统 TCP/UDP 实际监听的端口（指定范围内）"""
    used: set[int] = set()
    try:
        import subprocess
        r = subprocess.run(
            ["ss", "-tulnH"],
            capture_output=True, text=True, check=False,
        )
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5:
                local = parts[4]
                port_str = local.rsplit(":", 1)[-1] if ":" in local else ""
                try:
                    port = int(port_str)
                    if port_min <= port <= port_max:
                        used.add(port)
                except ValueError:
                    pass
    except Exception:
        pass
    return used


def _ensure_xray_template_service(xray_path: str) -> None:
    """确保 systemd 模板单元 xray@.service 存在（多隧道独立实例用）"""
    from pathlib import Path
    import subprocess
    from wireguard.constants import XRAY_DOC_URL
    tpl_path = Path("/etc/systemd/system/xray@.service")
    if not tpl_path.exists():
        tpl_path.write_text(
            "[Unit]\n"
            "Description=Xray Service - %i\n"
            f"Documentation={XRAY_DOC_URL}\n"
            "After=network.target nss-lookup.target\n\n"
            "[Service]\n"
            "User=nobody\n"
            "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE\n"
            "AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE\n"
            "NoNewPrivileges=true\n"
            f"ExecStart={xray_path} run -config /usr/local/etc/xray/%i.json\n"
            "Restart=on-failure\n"
            "RestartPreventExitStatus=23\n"
            "LimitNPROC=10000\n"
            "LimitNOFILE=1000000\n"
            "RuntimeDirectory=xray\n"
            "RuntimeDirectoryMode=0755\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)


def install_client(token: str | None = None) -> None:
    """私网客户端安装向导"""
    from core.i18n import t

    breadcrumb = ["OpsKit", t("menu.software"), t("software.wireguard"), t("software.wg_client")]
    _install_client_token(breadcrumb, token=token)


def _install_client_token(breadcrumb: list[str], token: str | None = None) -> None:
    """令牌模式安装客户端（支持多隧道）

    Args:
        breadcrumb: 面包屑路径
        token: 连接令牌（传入时跳过交互输入，用于 CLI 非交互模式）
    """
    from core.i18n import t
    from core.prompt import text_input, pause, clear_screen, UserCancel
    from core.theme import print_error, print_action_title
    from core.progress import MultiStepProgress
    from wireguard.constants import (
        WG_UDP_PORT,
        WG_CONFIG_DIR, XRAY_CONFIG_DIR, WG_SERVICE, XRAY_SERVICE,
        WG_CLIENT_MTU, WG_KEEPALIVE, XRAY_BINARY,
    )
    from wireguard.utils import (
        detect_default_iface, enable_and_start, stop_and_disable, write_file,
        get_os_id, install_wireguard_pkg, install_xray,
    )
    from wireguard.templates import xray_client_config, wg_client_config
    from wireguard.token import decode_token

    # ── 获取令牌（CLI 传入或交互输入）───────────────────────────────
    if token:
        raw_token = token.strip()
    else:
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

    server_ip     = data["server_ip"]
    server_port   = data["server_port"]
    wg_server_pub = data["wg_server_pubkey"]
    wg_client_priv = data["wg_client_privkey"]
    wg_psk        = data["wg_psk"]
    client_ip     = data["client_ip"]
    uuid          = data["uuid"]
    sni           = data["sni"]
    vpn_subnet    = data.get("vpn_subnet",  "10.10.10.0/24")
    vpn_gateway   = data.get("vpn_gateway", "10.10.10.1")
    label         = data.get("label",       "default")
    dns           = data.get("dns")

    import re as _re
    label = _re.sub(r"[^a-zA-Z0-9_-]", "-", label)[:24].strip("-") or "default"
    wg_iface      = f"wg-{label}"
    xray_instance = label

    wg_port  = WG_UDP_PORT
    iface    = detect_default_iface()

    # ── 已安装隧道检查：防止重复安装同一 label ──────────────────
    _state = _load_client_state()
    _tunnels: list[dict] = _state.get("tunnels", [])
    _dup = next((t_ for t_ in _tunnels if t_.get("label") == label), None)
    if _dup:
        from core.theme import print_warning
        print_warning(t("wireguard.tunnel_label_dup", label=label))
        pause()
        return

    # ── 自动分配本地端口 ────────────────────────────────────────────
    local_port = _alloc_local_port(_tunnels)

    # ── 服务端连通性预检 ──────────────────────────────────────────
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

    # ── 安装步骤 ─────────────────────────────────────────────────────
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
        from pathlib import Path as _Path

        sp.step(t("wireguard.step.check_os"))
        os_id = get_os_id()
        if os_id not in ("debian", "ubuntu", "centos", "rocky", "almalinux", "rhel", "fedora"):
            raise InstallError(t("wireguard.error.unsupported_os", os_id=os_id))

        from core.sysconfig import SysConfigManager as _SCM
        _SCM.save(f"wg_client_{label}", sysparams=None, pre_install={})

        sp.step(t("wireguard.step.install_wg"))
        install_wireguard_pkg(os_id)

        sp.step(t("wireguard.step.install_xray"))
        install_xray()
        _ensure_xray_template_service(XRAY_BINARY)

        # ── 写入 Xray 配置（/usr/local/etc/xray/{label}.json）
        sp.step(t("wireguard.step.write_xray_config"))
        xray_cfg_path = f"/usr/local/etc/xray/{xray_instance}.json"
        xray_cfg = xray_client_config(
            sni=sni,
            server_port=server_port,
            uuid=uuid,
            local_port=local_port,
            wg_port=wg_port,
        )
        write_file(xray_cfg_path, xray_cfg)

        # ── 写入 WireGuard 配置（/etc/wireguard/{wg_iface}.conf）
        sp.step(t("wireguard.step.write_wg_config"))
        wg_cfg_path = f"/etc/wireguard/{wg_iface}.conf"
        _local_dns = _detect_local_dns(iface, vpn_dns=dns)
        _final_dns = _merge_dns(vpn_dns=dns, local_dns=_local_dns)
        wg_cfg = wg_client_config(
            client_private_key=wg_client_priv,
            client_ip=client_ip,
            server_public_key=wg_server_pub,
            psk=wg_psk,
            server_endpoint=f"127.0.0.1:{local_port}",
            mtu=WG_CLIENT_MTU,
            keepalive=WG_KEEPALIVE,
            vpn_subnet=vpn_subnet,
            dns=_final_dns,
        )
        write_file(wg_cfg_path, wg_cfg)

        # ── 启动服务（xray@{label} 先起，再起 wg-quick@{wg_iface}）
        sp.step(t("wireguard.step.start_services"))
        xray_svc = f"xray@{xray_instance}"
        wg_svc   = f"wg-quick@{wg_iface}"
        _dropin_dir = _Path(f"/etc/systemd/system/{wg_svc}.service.d")
        _dropin_dir.mkdir(parents=True, exist_ok=True)
        (_dropin_dir / "after-xray.conf").write_text(
            f"[Unit]\nAfter={xray_svc}.service\nWants={xray_svc}.service\n"
        )
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        subprocess.run(["wg-quick", "down", wg_iface], check=False, capture_output=True)
        stop_and_disable(wg_svc)
        stop_and_disable(xray_svc)
        enable_and_start(xray_svc)
        enable_and_start(wg_svc)
        _SCM.mark_installed(f"wg_client_{label}")

        sp.step(t("wireguard.step.verify"))
        import time
        time.sleep(2)
        from wireguard.utils import is_service_active
        _xray_ok = is_service_active(xray_svc)
        _wg_ok   = is_service_active(wg_svc)
        ping_ok = False
        try:
            r = subprocess.run(
                ["ping", "-c", "2", "-W", "2", vpn_gateway],
                capture_output=True, text=True, check=False,
            )
            ping_ok = (r.returncode == 0)
        except Exception:
            pass
        if not (_xray_ok and _wg_ok):
            from core.theme import print_warning as _pw
            _pw(f"{xray_svc}={'active' if _xray_ok else 'INACTIVE'} {wg_svc}={'active' if _wg_ok else 'INACTIVE'}")

    # ── 保存 tunnels state ──────────────────────────────────────────────
    _tunnels.append({
        "label":       label,
        "wg_iface":    wg_iface,
        "xray_svc":    f"xray@{xray_instance}",
        "local_port":  local_port,
        "client_ip":   client_ip,
        "vpn_gateway": vpn_gateway,
        "vpn_subnet":  vpn_subnet,
        "server_ip":   server_ip,
        "server_port": server_port,
        "sni":         sni,
        "uuid":        uuid,
        "wg_server_pub": wg_server_pub,
        "dns":         _final_dns or "",
        "token":       raw_token.strip(),
    })
    _state["tunnels"] = _tunnels
    _save_client_state(_state)

    # ── 安装成功 Panel ────────────────────────────────────────────────
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text as _Text
    from core.prompt import pause
    clear_screen()
    print_action_title(breadcrumb)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    _ping_text = _Text(vpn_gateway, style="#a6e3a1" if ping_ok else "#f38ba8")
    rows = [
        (t("wireguard.client_diagnose.client_ip"), client_ip),
        (t("wireguard.info.vpn_gateway"),          vpn_gateway),
        (t("wireguard.manage.peer_name"),          label),
        (t("wireguard.info.xray_port"),            str(server_port)),
        (t("wireguard.info.domain"),               sni),
        (t("wireguard.info.dns"),                  _final_dns or "—"),
    ]
    for lbl_, val_ in rows:
        tbl.add_row(_Text(lbl_, style="#7f849c"), _Text(val_, style="bold #cdd6f4"))
    tbl.add_row(_Text(t("wireguard.client_diagnose.ping_gateway"), style="#7f849c"), _ping_text)
    console.print(Panel(
        tbl,
        title=f"[bold #a6e3a1]{t('wireguard.install_client_success')}[/bold #a6e3a1]",
        border_style="#a6e3a1",
        padding=(1, 2),
    ))
    pause()


def uninstall_client() -> None:
    """私网客户端卸载（所有隧道）"""
    from core.i18n import t
    from core.theme import print_success, print_action_title
    from core.progress import MultiStepProgress
    from wireguard.constants import WG_CONFIG_DIR, XRAY_CONFIG_DIR, XRAY_BINARY
    from wireguard.utils import stop_and_disable

    print_action_title(["OpsKit", t("menu.software"), t("software.wireguard"), t("software.wg_client_uninstall")])

    state = _load_client_state()
    tunnels: list[dict] = state.get("tunnels", [])

    step_keys = [
        "wireguard.step.client_uninstall_stop_wg",
        "wireguard.step.client_uninstall_stop_xray",
        "wireguard.step.client_uninstall_clean_config",
        "wireguard.step.client_uninstall_clean_routes",
    ]
    descs = [t(k) for k in step_keys]

    import shutil
    import subprocess
    from pathlib import Path as _Path

    with MultiStepProgress(descs) as sp:
        sp.step(descs[0])
        for tn in tunnels:
            wg_iface = tn.get("wg_iface", "wg0")
            wg_svc   = f"wg-quick@{wg_iface}"
            subprocess.run(["wg-quick", "down", wg_iface], check=False, capture_output=True)
            stop_and_disable(wg_svc)
            shutil.rmtree(f"/etc/systemd/system/{wg_svc}.service.d", ignore_errors=True)
            _Path(f"/etc/wireguard/{wg_iface}.conf").unlink(missing_ok=True)

        sp.step(descs[1])
        for tn in tunnels:
            xray_svc = tn.get("xray_svc", "xray")
            stop_and_disable(xray_svc)
            lbl = tn.get("label", "")
            if lbl:
                _Path(f"/usr/local/etc/xray/{lbl}.json").unlink(missing_ok=True)
        _Path("/etc/systemd/system/xray@.service").unlink(missing_ok=True)
        _Path("/etc/systemd/system/xray.service").unlink(missing_ok=True)

        sp.step(descs[2])
        _Path("/etc/sysctl.d/99-wg.conf").unlink(missing_ok=True)
        from core.sysconfig import SysConfigManager as _SCM
        for tn in tunnels:
            lbl = tn.get("label", "")
            _SCM.restore(f"wg_client_{lbl}")
            _SCM.remove(f"wg_client_{lbl}")
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        _Path(XRAY_BINARY).unlink(missing_ok=True)
        from core.paths import xray_log_dir, xray_data_dir
        for path in (xray_log_dir(), xray_data_dir()):
            shutil.rmtree(str(path), ignore_errors=True)

        sp.step(descs[3])
        _save_client_state({})

    print_success(t("wireguard.client_diagnose.uninstall_success"))


def diagnose_client() -> None:
    """私网客户端诊断（遍历所有隧道）"""
    import subprocess
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from core.i18n import t
    from core.theme import print_action_title
    from core.prompt import pause
    from wireguard.utils import is_service_active

    print_action_title(["OpsKit", t("menu.software"), t("software.wireguard"), t("software.diagnose")])

    dk = "wireguard.client_diagnose"
    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"
    _SEC   = "bold #89b4fa"

    def _lbl(s: str) -> Text: return Text(s, style=_LABEL)
    def _val(s: str) -> Text: return Text(s, style=_VALUE)
    def _sec(s: str) -> Text: return Text(f"── {s} ──", style=_SEC)
    def _dot(ok: bool) -> Text:
        return Text("● active", style="#a6e3a1") if ok else Text("● inactive", style="#f38ba8")

    state   = _load_client_state()
    tunnels: list[dict] = state.get("tunnels", [])

    if not tunnels:
        from core.theme import print_warning
        print_warning(t(f"{dk}.no_wg_conf"))
        pause()
        return

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    for tn in tunnels:
        lbl       = tn.get("label",       "?")
        wg_iface  = tn.get("wg_iface",    f"wg-{lbl}")
        xray_svc  = tn.get("xray_svc",    f"xray@{lbl}")
        wg_svc    = f"wg-quick@{wg_iface}"
        vpn_gw    = tn.get("vpn_gateway", "—")
        client_ip = tn.get("client_ip",   "—")
        server_ip = tn.get("server_ip",   "—")
        local_port = str(tn.get("local_port", "—"))
        sni_val   = tn.get("sni",         "—")
        _dns_val  = tn.get("dns", "") or ""
        if not _dns_val:
            try:
                import re as _re
                from pathlib import Path as _P
                _wg_conf = _P(f"/etc/wireguard/{wg_iface}.conf").read_text("utf-8")
                _m = _re.search(r"^DNS\s*=\s*(.+)$", _wg_conf, _re.MULTILINE)
                _dns_val = _m.group(1).strip() if _m else ""
            except Exception:
                pass

        wg_ok   = is_service_active(wg_svc)
        xray_ok = is_service_active(xray_svc)

        _ping_ok = False
        try:
            r = subprocess.run(
                ["ping", "-c", "2", "-W", "2", vpn_gw],
                capture_output=True, text=True, check=False,
            )
            _ping_ok = (r.returncode == 0)
        except Exception:
            pass

        tbl.add_row(_sec(f"{lbl}  ({wg_iface})"), Text(""))
        tbl.add_row(_lbl(wg_svc),   _dot(wg_ok))
        tbl.add_row(_lbl(xray_svc), _dot(xray_ok))
        tbl.add_row(_lbl(t(f"{dk}.client_ip")),  _val(client_ip))
        tbl.add_row(_lbl(t(f"{dk}.server_ip")),  _val(server_ip))
        tbl.add_row(_lbl(t("wireguard.info.domain")), _val(sni_val))
        tbl.add_row(_lbl(t(f"{dk}.xray_local_port")), _val(local_port))
        tbl.add_row(_lbl(t("wireguard.info.dns")), _val(_dns_val or "—"))
        ping_text = Text(
            vpn_gw,
            style="#a6e3a1" if _ping_ok else "#f38ba8",
        )
        tbl.add_row(_lbl(t(f"{dk}.ping_gateway")), ping_text)
        if tn is not tunnels[-1]:
            tbl.add_row(Text(""), Text(""))

    console.print(Panel(
        tbl,
        title=f"[bold]{t(f'{dk}.title')}[/bold]",
        border_style="#89b4fa",
        padding=(1, 2),
    ))
    pause()


def manage_client() -> None:
    """客户端管理主菜单（隧道列表 / 查看 / 更新令牌 / 移除单条隧道）"""
    from core.i18n import t
    from core.prompt import select, UserCancel, pause
    from core.theme import get_icon, get_color, print_action_title, print_warning

    mk = "wireguard.client_manage"
    breadcrumb = ["OpsKit", t("menu.software"), t("software.wireguard"),
                  t("software.wg_client"), t(f"{mk}.title")]
    muted = get_color("muted")

    while True:
        state    = _load_client_state()
        tunnels  = state.get("tunnels", [])
        installed = len(tunnels) > 0

        if installed:
            choices = [
                {"key": "1", "label": f"{get_icon('search')} {t(f'{mk}.view')}"},
                {"key": "2", "label": f"{get_icon('update_token')} {t(f'{mk}.update_token')}"},
                {"key": "3", "label": f"{get_icon('delete')} {t(f'{mk}.remove_tunnel')}"},
            ]
        else:
            choices = [
                {"key": "1", "label": f"[{muted}]{get_icon('search')} {t(f'{mk}.view')}[/{muted}]", "disabled": True},
                {"key": "2", "label": f"[{muted}]{get_icon('update_token')} {t(f'{mk}.update_token')}[/{muted}]", "disabled": True},
                {"key": "3", "label": f"[{muted}]{get_icon('delete')} {t(f'{mk}.remove_tunnel')}[/{muted}]", "disabled": True},
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
        elif key == "3":
            remove_tunnel(breadcrumb)


def view_client_info(breadcrumb: list[str]) -> None:
    """查看所有隧道连接信息"""
    from core.i18n import t
    from core.prompt import pause, clear_screen
    from core.theme import print_action_title, print_warning
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    mk = "wireguard.client_manage"
    sub = [*breadcrumb, t(f"{mk}.view")]

    state   = _load_client_state()
    tunnels = state.get("tunnels", [])
    if not tunnels:
        print_warning(t(f"{mk}.no_state"))
        pause()
        return

    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"
    _TOKEN = "#6c7086"
    _SEC   = "bold #89b4fa"

    def _trunc(s: str, n: int = 56) -> str:
        return s if len(s) <= n else s[:n] + "..."

    clear_screen()
    print_action_title(sub)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    for tn in tunnels:
        lbl      = tn.get("label", "?")
        wg_iface = tn.get("wg_iface", f"wg-{lbl}")
        _vi_dns  = tn.get("dns", "") or ""
        if not _vi_dns:
            try:
                import re as _re2
                from pathlib import Path as _P2
                _wg_conf2 = _P2(f"/etc/wireguard/{wg_iface}.conf").read_text("utf-8")
                _m2 = _re2.search(r"^DNS\s*=\s*(.+)$", _wg_conf2, _re2.MULTILINE)
                _vi_dns = _m2.group(1).strip() if _m2 else ""
            except Exception:
                pass
        tbl.add_row(Text(f"── {lbl} ──", style=_SEC), Text(""))
        rows = [
            (t(f"{mk}.field_ip"),     tn.get("client_ip",   "—")),
            (t(f"{mk}.field_server"), tn.get("server_ip",   "—")),
            (t(f"{mk}.field_port"),   str(tn.get("server_port", "—"))),
            (t(f"{mk}.field_sni"),    tn.get("sni",         "—")),
            (t(f"{mk}.field_uuid"),   tn.get("uuid",        "—")),
            (t(f"{mk}.field_dns"),    _vi_dns or "—"),
        ]
        for _l, _v in rows:
            tbl.add_row(Text(_l, style=_LABEL), Text(_v, style=_VALUE))
        token = tn.get("token", "")
        tbl.add_row(Text(""), Text(""))
        tbl.add_row(Text(t(f"{mk}.field_token"), style=_LABEL),
                    Text(_trunc(token), style=_TOKEN))
        tbl.add_row(Text(""), Text(""))

    console.print(Panel(
        tbl,
        title=f"[bold #89b4fa]{t(f'{mk}.view_title')}[/bold #89b4fa]",
        border_style="#89b4fa",
        padding=(1, 2),
    ))
    pause()


def remove_tunnel(breadcrumb: list[str]) -> None:
    """移除单条隧道（停止服务 + 删除配置 + 更新 state）"""
    import subprocess
    import shutil
    from pathlib import Path as _Path
    from core.i18n import t
    from core.prompt import pause, select, UserCancel
    from core.theme import print_error, print_action_title, print_success
    from wireguard.utils import stop_and_disable

    mk = "wireguard.client_manage"
    sub = [*breadcrumb, t(f"{mk}.remove_tunnel")]

    state   = _load_client_state()
    tunnels = state.get("tunnels", [])
    if not tunnels:
        print_error(t(f"{mk}.no_state"))
        pause()
        return

    choices = [{"key": str(i + 1), "label": tn.get("label", f"tunnel-{i}")} for i, tn in enumerate(tunnels)]
    try:
        key = select(
            breadcrumb=sub,
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key="software",
            back_label=t("menu.back"),
        )
    except UserCancel:
        return
    if key is None:
        return

    idx = int(key) - 1
    tn = tunnels[idx]
    wg_iface = tn.get("wg_iface", "")
    xray_svc = tn.get("xray_svc", "")
    lbl      = tn.get("label",    "")

    if wg_iface:
        subprocess.run(["wg-quick", "down", wg_iface], check=False, capture_output=True)
        stop_and_disable(f"wg-quick@{wg_iface}")
        shutil.rmtree(f"/etc/systemd/system/wg-quick@{wg_iface}.service.d", ignore_errors=True)
        _Path(f"/etc/wireguard/{wg_iface}.conf").unlink(missing_ok=True)
    if xray_svc:
        stop_and_disable(xray_svc)
    if lbl:
        _Path(f"/usr/local/etc/xray/{lbl}.json").unlink(missing_ok=True)
        from core.sysconfig import SysConfigManager as _SCM
        _SCM.restore(f"wg_client_{lbl}")
        _SCM.remove(f"wg_client_{lbl}")
    subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)

    tunnels.pop(idx)
    state["tunnels"] = tunnels
    _save_client_state(state)
    print_success(t(f"{mk}.remove_tunnel_ok").format(label=lbl))
    pause()


def update_client_token(breadcrumb: list[str]) -> None:
    """更新指定隧道令牌：选择隧道 → 解析新令牌 → 重写配置并重启服务"""
    import subprocess
    from core.i18n import t
    from core.prompt import pause, clear_screen, text_input, select, UserCancel
    from core.theme import print_error, print_action_title, print_success
    from core.progress import MultiStepProgress
    from wireguard.constants import WG_UDP_PORT, WG_CLIENT_MTU, WG_KEEPALIVE
    from wireguard.utils import enable_and_start, stop_and_disable, write_file, detect_default_iface
    from wireguard.templates import xray_client_config, wg_client_config
    from wireguard.token import decode_token

    mk  = "wireguard.client_manage"
    sub = [*breadcrumb, t(f"{mk}.update_token")]

    state   = _load_client_state()
    tunnels = state.get("tunnels", [])
    if not tunnels:
        print_error(t(f"{mk}.no_state"))
        pause()
        return

    choices = [{"key": str(i + 1), "label": tn.get("label", f"tunnel-{i}")} for i, tn in enumerate(tunnels)]
    try:
        key = select(
            breadcrumb=sub,
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key="software",
            back_label=t("menu.back"),
        )
    except UserCancel:
        return
    if key is None:
        return

    idx = int(key) - 1
    tn  = tunnels[idx]

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

    server_ip      = data["server_ip"]
    server_port    = data["server_port"]
    wg_server_pub  = data["wg_server_pubkey"]
    wg_client_priv = data["wg_client_privkey"]
    wg_psk         = data["wg_psk"]
    client_ip      = data["client_ip"]
    uuid           = data["uuid"]
    sni            = data["sni"]
    vpn_subnet     = data.get("vpn_subnet",  tn.get("vpn_subnet",  "10.10.10.0/24"))
    vpn_gateway    = data.get("vpn_gateway", tn.get("vpn_gateway", "10.10.10.1"))

    wg_iface   = tn.get("wg_iface",   f"wg-{tn.get('label', 'default')}")
    xray_svc   = tn.get("xray_svc",   f"xray@{tn.get('label', 'default')}")
    local_port = tn.get("local_port",  4000)
    wg_svc     = f"wg-quick@{wg_iface}"
    lbl        = tn.get("label",       "default")
    xray_cfg_path = f"/usr/local/etc/xray/{lbl}.json"
    wg_cfg_path   = f"/etc/wireguard/{wg_iface}.conf"

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
            sni=sni, server_port=server_port, uuid=uuid,
            local_port=local_port, wg_port=WG_UDP_PORT,
        )
        write_file(xray_cfg_path, xray_cfg)

        sp.step(step_descs[1])
        _upd_vpn_dns = data.get("dns")
        _upd_iface   = detect_default_iface()
        _upd_local   = _detect_local_dns(_upd_iface, vpn_dns=_upd_vpn_dns)
        _upd_dns     = _merge_dns(vpn_dns=_upd_vpn_dns, local_dns=_upd_local)
        wg_cfg = wg_client_config(
            client_private_key=wg_client_priv, client_ip=client_ip,
            server_public_key=wg_server_pub, psk=wg_psk,
            server_endpoint=f"127.0.0.1:{local_port}",
            mtu=WG_CLIENT_MTU, keepalive=WG_KEEPALIVE, vpn_subnet=vpn_subnet,
            dns=_upd_dns,
        )
        write_file(wg_cfg_path, wg_cfg)

        sp.step(step_descs[2])
        subprocess.run(["wg-quick", "down", wg_iface], check=False, capture_output=True)
        stop_and_disable(wg_svc)
        stop_and_disable(xray_svc)
        enable_and_start(xray_svc)
        enable_and_start(wg_svc)

        sp.step(step_descs[3])
        import time
        time.sleep(2)
        from wireguard.utils import is_service_active
        _xray_ok = is_service_active(xray_svc)
        _wg_ok   = is_service_active(wg_svc)

    tn.update({
        "token":       raw_token.strip(),
        "client_ip":   client_ip,
        "server_ip":   server_ip,
        "server_port": server_port,
        "sni":         sni,
        "uuid":        uuid,
        "wg_server_pub": wg_server_pub,
        "vpn_subnet":  vpn_subnet,
        "vpn_gateway": vpn_gateway,
    })
    tunnels[idx] = tn
    state["tunnels"] = tunnels
    _save_client_state(state)

    clear_screen()
    print_action_title(sub)
    if _xray_ok and _wg_ok:
        print_success(t(f"{mk}.update_success"))
    else:
        from core.theme import print_warning
        print_warning(
            f"{xray_svc}={'active' if _xray_ok else 'INACTIVE'} "
            f"{wg_svc}={'active' if _wg_ok else 'INACTIVE'}"
        )
    pause()
