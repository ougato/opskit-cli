"""WireGuard 公网服务端安装 / 卸载 / 诊断 / 管理逻辑"""
from __future__ import annotations

from software.base import InstallError, UninstallError
from core.constants import PUBLIC_IP_APIS, GITHUB_BASE
from core.theme import console
from wireguard.constants import (
    ACME_INSTALL_MIRRORS,
    XRAY_API_LATEST, XRAY_API_LATEST_GHPROXY,
    XRAY_DOWNLOAD_ZIP, XRAY_REPO,
    XRAY_DOC_URL,
)


def _print_qr(data: str) -> None:
    """输出二维码到终端。优先 qrencode CLI (ANSIUTF8)，兜底 Python qrcode 半块字符。"""
    import shutil, subprocess, sys
    if shutil.which("qrencode"):
        _r = subprocess.run(
            ["qrencode", "-t", "ANSIUTF8", "-o", "-", "-m", "2"],
            input=data, capture_output=True, text=True, check=False,
        )
        if _r.returncode == 0 and _r.stdout.strip():
            sys.stdout.write(_r.stdout)
            sys.stdout.flush()
            return
    try:
        import qrcode as _qrcode  # type: ignore
        _qr = _qrcode.QRCode(border=2)
        _qr.add_data(data)
        _qr.make(fit=True)
        _matrix = _qr.get_matrix()
        _rows = len(_matrix)
        _output = []
        for _ri in range(0, _rows, 2):
            _top = _matrix[_ri]
            _bot = _matrix[_ri + 1] if _ri + 1 < _rows else [False] * len(_top)
            _line = ""
            for _t, _b in zip(_top, _bot):
                if _t and _b:
                    _line += "\033[40m \033[0m"
                elif _t and not _b:
                    _line += "\033[40;97m▄\033[0m"
                elif not _t and _b:
                    _line += "\033[40;97m▀\033[0m"
                else:
                    _line += "\033[47m \033[0m"
            _output.append(_line)
        sys.stdout.write("\n".join(_output) + "\n")
        sys.stdout.flush()
    except ImportError:
        console.print(f"[#cdd6f4]{data}[/#cdd6f4]")


def _build_vless_uri(uuid: str, sni: str, server_ip: str, port: int = 443,
                     ws_path: str = "/vless-ws", label: str = "") -> str:
    """生成 v2rayNG / Hiddify 可识别的 vless:// 分享链接。
    坑点 1：path 必须 URL 编码（/ → %2F），否则部分客户端解析失败。
    坑点 2：address 必须用域名而非国内 IP。v2rayNG 默认路由规则对国内 IP
           走直连（bypass），导致流量不经过 VLESS 代理隧道直接发出，
           服务端收到的是裸 TLS 而非 VLESS 流量，握手失败。
           用域名时 v2rayNG 正确走代理出站，SNI 由 sni 参数单独指定。
    坑点 3：domainStrategy 仅支持 JSON 配置，vless:// URI 中传入会导致
           v2rayNG 解析异常，TLS 握手后立即发 RST，禁止加入 URI。
    """
    from urllib.parse import quote
    _path = quote(ws_path, safe="")
    _label = quote(label or sni, safe="")
    return (
        f"vless://{uuid}@{sni}:{port}"
        f"?encryption=none&security=tls&type=ws"
        f"&host={sni}&path={_path}&fp=chrome"
        f"#{_label}"
    )


def _detect_public_ip() -> str:
    """检测本机公网 IP"""
    import urllib.request
    apis = PUBLIC_IP_APIS
    for url in apis:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "opskit/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                ip = resp.read().decode("utf-8").strip()
                if ip and "." in ip:
                    return ip
        except Exception:
            continue
    return ""


def _save_state(state: dict) -> None:
    """保存服务端状态文件"""
    import json
    from wireguard.constants import WG_STATE_FILE
    from wireguard.utils import write_file
    write_file(WG_STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False))


def _load_state() -> dict:
    """加载服务端状态文件"""
    import json
    from pathlib import Path
    from wireguard.constants import WG_STATE_FILE
    p = Path(WG_STATE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _generate_client_token(
    server_ip: str,
    server_port: int,
    wg_server_pub: str,
    reality_pub: str,
    uuid: str,
    short_id: str,
    sni: str,
    client_ip: str,
    wg_client_priv: str,
    wg_psk: str,
    vpn_subnet: str = "10.10.10.0/24",
    vpn_gateway: str = "10.10.10.1",
    label: str = "default",
    dns: str | None = None,
) -> str:
    """生成客户端连接令牌（v2 格式，含子网/网关/标签）"""
    from wireguard.token import encode_token
    data: dict = {
        "server_ip": server_ip,
        "server_port": server_port,
        "wg_server_pubkey": wg_server_pub,
        "wg_client_privkey": wg_client_priv,
        "wg_psk": wg_psk,
        "client_ip": client_ip,
        "reality_pubkey": reality_pub,
        "uuid": uuid,
        "short_id": short_id,
        "sni": sni,
        "vpn_subnet": vpn_subnet,
        "vpn_gateway": vpn_gateway,
        "label": label,
    }
    if dns:
        data["dns"] = dns
    return encode_token(data)


def _setup_dnsmasq(vpn_gateway: str, base_domain: str, os_id: str) -> None:
    """安装并配置 dnsmasq — 安全、幂等、不覆盖用户配置"""
    import shutil
    import subprocess
    from pathlib import Path
    from wireguard.constants import DNSMASQ_CONF_PATH, DNSMASQ_UPSTREAM_DNS

    if not shutil.which("dnsmasq"):
        if os_id in ("debian", "ubuntu"):
            subprocess.run(["apt-get", "install", "-y", "dnsmasq"],
                           check=True, capture_output=True, text=True)
        else:
            subprocess.run(["yum", "install", "-y", "dnsmasq"],
                           check=True, capture_output=True, text=True)

    upstream_lines = "\n".join(f"server={s}" for s in DNSMASQ_UPSTREAM_DNS)
    new_content = (
        f"# opskit WireGuard DNS 配置 — 由 opskit 自动管理，请勿手动修改\n"
        f"# 仅监听 WG 接口，不影响服务器自身 DNS\n"
        f"listen-address={vpn_gateway}\n"
        f"bind-interfaces\n"
        f"# 泛域名解析到 WG 网关\n"
        f"address=/{base_domain}/{vpn_gateway}\n"
        f"# 上游 DNS\n"
        f"{upstream_lines}\n"
    )
    conf_path = Path(DNSMASQ_CONF_PATH)
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    if conf_path.exists() and conf_path.read_text("utf-8") == new_content:
        pass
    else:
        conf_path.write_text(new_content, encoding="utf-8")

    subprocess.run(["systemctl", "enable", "--now", "dnsmasq"],
                   check=False, capture_output=True, text=True)
    subprocess.run(["systemctl", "restart", "dnsmasq"],
                   check=False, capture_output=True, text=True)


def install_server() -> None:
    """公网服务端安装向导"""
    from core.i18n import t
    from core.theme import print_action_title
    from core.progress import MultiStepProgress
    from core.prompt import pause, clear_screen, text_input, UserCancel
    from wireguard.constants import (
        XRAY_REALITY_PORT,
        WG_UDP_PORT,
        WG_CONFIG_FILE, XRAY_CONFIG_FILE, WG_SERVICE, XRAY_SERVICE,
        NGINX_VLESS_WS_CONF, NGINX_STREAM_CONF, NGINX_STEAL_CONF,
        XRAY_WS_PORT, XRAY_WS_PATH, ACME_CERT_DIR,
        VPN_CLIENT_IP_START,
        VPN_SUBNET_TPL, VPN_GW_TPL, VPN_DEFAULT_OCTET3,
    )
    from wireguard.utils import (
        gen_wg_keypair, gen_wg_psk, gen_xray_keypair, gen_uuid, gen_short_id,
        detect_default_iface, enable_and_start, write_file,
        get_os_id, install_wireguard_pkg, install_xray,
    )
    from wireguard.templates import (
        xray_server_config_ws,
        wg_server_config, wg_peer_section,
        nginx_vless_ws_config, nginx_http_only_config,
    )

    breadcrumb = ["OpsKit", t("menu.software"), t("software.wireguard"), t("software.install")]

    from core.config import load_config, set_config_value
    _cfg = load_config()
    _wg_cfg = _cfg.get("wireguard") or {}
    _saved_domain = _wg_cfg.get("domain") or ""
    _saved_octet3 = _wg_cfg.get("octet3")
    _saved_label  = _wg_cfg.get("tunnel_label") or ""

    # ── 收集输入 ─────────────────────────────────────────────────────────────
    try:
        sni = text_input(
            breadcrumb=breadcrumb,
            prompt=t("wireguard.input_domain"),
            default=_saved_domain,
            theme_key="software",
        )
    except UserCancel:
        return
    if not sni or not sni.strip():
        from core.theme import print_error
        print_error(t("wireguard.domain_required"))
        pause()
        return
    sni = sni.strip()
    if sni != _saved_domain:
        _cfg = set_config_value(_cfg, "wireguard.domain", sni)
    cert_email = _get_acme_email()

    public_ip = _detect_public_ip()
    if not public_ip:
        try:
            public_ip = text_input(
                breadcrumb=breadcrumb,
                prompt=t("wireguard.input_public_ip"),
                theme_key="software",
            )
        except UserCancel:
            return
        if not public_ip or not public_ip.strip():
            from core.theme import print_error
            print_error(t("wireguard.public_ip_required"))
            pause()
            return
        public_ip = public_ip.strip()

    # ── 输入 VPN 子网第三段（1~254）────────────────────────────────────────
    _default_octet3 = _saved_octet3 if _saved_octet3 is not None else VPN_DEFAULT_OCTET3
    _octet3 = _default_octet3
    while True:
        try:
            _octet3_raw = text_input(
                breadcrumb=breadcrumb,
                prompt=t("wireguard.input_vpn_octet3"),
                default=str(_default_octet3),
                theme_key="software",
            )
        except UserCancel:
            return
        _raw = (_octet3_raw or "").strip() or str(_default_octet3)
        try:
            _val = int(_raw)
            if 1 <= _val <= 254:
                _octet3 = _val
                break
        except ValueError:
            pass
        from core.theme import print_error as _pe
        _pe(t("wireguard.input_vpn_octet3_invalid"))

    if _octet3 != _saved_octet3:
        _cfg = set_config_value(_cfg, "wireguard.octet3", _octet3)

    vpn_subnet = VPN_SUBNET_TPL.format(octet3=_octet3)
    vpn_gateway = VPN_GW_TPL.format(octet3=_octet3)

    # ── 输入隧道名称 ──────────────────────────────────────────────────────
    _default_label = _saved_label or "server"
    try:
        _label_raw = text_input(
            breadcrumb=breadcrumb,
            prompt=t("wireguard.input_tunnel_label"),
            default=_default_label,
            theme_key="software",
        )
    except UserCancel:
        return
    tunnel_label = (_label_raw or "").strip() or _default_label
    import re as _re
    tunnel_label = _re.sub(r"[^a-zA-Z0-9_-]", "-", tunnel_label)[:24].strip("-") or _default_label

    if tunnel_label != _saved_label:
        _cfg = set_config_value(_cfg, "wireguard.tunnel_label", tunnel_label)

    iface = detect_default_iface()
    wg_port = WG_UDP_PORT
    short_id = gen_short_id()
    server_port = XRAY_REALITY_PORT

    # ── 443 端口检测（MultiStepProgress 外，交互先于安装）───────────────
    _port_status, _port_proc = _check_port_443()
    nginx_mode = "install"
    if _port_status == "other":
        from core.theme import print_warning
        print_warning(t("wireguard.port_443_other_occupied").format(proc=_port_proc))
        from core.prompt import confirm as _confirm
        try:
            if not _confirm(breadcrumb=breadcrumb, prompt=t("wireguard.port_443_other_confirm")):
                from core.theme import print_error
                print_error(t("wireguard.port_443_other_abort"))
                pause()
                return
        except UserCancel:
            return
        nginx_mode = "install"
    elif _port_status == "nginx":
        from core.theme import console as _c443
        _c443.print(f"[#a6e3a1]{t('wireguard.port_443_nginx_auto')}[/#a6e3a1]")
        nginx_mode = "append"

    # ── 安装步骤 ─────────────────────────────────────────────────────────────
    step_keys = [
        "wireguard.step.check_os",
        "wireguard.step.install_wg",
        "wireguard.step.install_xray",
    ]
    if nginx_mode == "install":
        step_keys.append("wireguard.step.install_nginx")
    step_keys += [
        "wireguard.step.issue_cert",
        "wireguard.step.config_nginx",
        "wireguard.step.gen_keys",
        "wireguard.step.write_xray_config",
        "wireguard.step.write_wg_config",
        "wireguard.step.gen_client",
        "wireguard.step.install_dnsmasq",
        "wireguard.step.start_services",
        "wireguard.step.verify",
    ]

    clear_screen()
    print_action_title(breadcrumb)
    with MultiStepProgress([t(s) for s in step_keys]) as sp:
        import subprocess

        # ── 检测 OS ────────────────────────────────────────────────────────────────────
        sp.step(t("wireguard.step.check_os"))
        os_id = get_os_id()
        if os_id not in ("debian", "ubuntu", "centos", "rocky", "almalinux", "rhel", "fedora"):
            raise InstallError(t("wireguard.error.unsupported_os", os_id=os_id))
        _ensure_system_deps(os_id)
        import shutil as _shutil_qr
        if not _shutil_qr.which("qrencode"):
            if os_id in ("debian", "ubuntu"):
                subprocess.run(["apt-get", "install", "-y", "qrencode"],
                               check=False, capture_output=True, text=True)
            else:
                subprocess.run(["yum", "install", "-y", "qrencode"],
                               check=False, capture_output=True, text=True)
        if not _shutil_qr.which("qrencode"):
            try:
                import qrcode as _qr_check  # noqa: F401
            except ImportError:
                subprocess.run(
                    ["pip3", "install", "-q", "qrcode"],
                    check=False, capture_output=True, text=True,
                )

        from core.sysconfig import SysConfigManager
        SysConfigManager.save(
            "wg_server",
            sysparams={"net.ipv4.ip_forward": "1"},
            pre_install={"nginx_installed_by_opskit": nginx_mode == "install"},
        )

        # ── 安装 WireGuard ───────────────────────────────────────────────────
        sp.step(t("wireguard.step.install_wg"))
        install_wireguard_pkg(os_id)

        # ── 安装 xray ────────────────────────────────────────────────────────
        sp.step(t("wireguard.step.install_xray"))
        install_xray()

        # ── 安装 / 配置 nginx（根据场景自动选择 install / append）───────────────────
        if nginx_mode == "install":
            sp.step(t("wireguard.step.install_nginx"))
        _setup_nginx(
            os_id=os_id, nginx_mode=nginx_mode,
            sni=sni, ws_port=XRAY_WS_PORT,
            cert_dir=ACME_CERT_DIR, ws_path=XRAY_WS_PATH,
            sp=sp,
        )

        sp.step(t("wireguard.step.issue_cert"))
        _issue_cert(sni, cert_email, ACME_CERT_DIR)

        sp.step(t("wireguard.step.config_nginx"))
        nginx_cfg = nginx_vless_ws_config(
            sni=sni,
            ws_port=XRAY_WS_PORT,
            cert_dir=ACME_CERT_DIR,
            ws_path=XRAY_WS_PATH,
        )
        write_file(NGINX_VLESS_WS_CONF, nginx_cfg)
        subprocess.run(["nginx", "-t"], check=True, capture_output=True, text=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=False,
                       capture_output=True, text=True)

        # ── 生成密钥 ─────────────────────────────────────────────────────────
        sp.step(t("wireguard.step.gen_keys"))
        wg_server_priv, wg_server_pub = gen_wg_keypair()
        xray_priv, xray_pub = gen_xray_keypair()
        uuid = gen_uuid()

        # ── 写入 xray 配置 ─────────────────────────────────────────────────────────────
        sp.step(t("wireguard.step.write_xray_config"))
        xray_cfg = xray_server_config_ws(
            uuid=uuid,
            ws_port=XRAY_WS_PORT,
            ws_path=XRAY_WS_PATH,
        )
        write_file(XRAY_CONFIG_FILE, xray_cfg)

        # ── 写入 WG 配置 ─────────────────────────────────────────────────────
        sp.step(t("wireguard.step.write_wg_config"))
        wg_cfg = wg_server_config(
            server_private_key=wg_server_priv,
            server_ip=vpn_gateway,
            wg_port=wg_port,
            iface=iface,
            vpn_subnet=vpn_subnet,
        )
        write_file(WG_CONFIG_FILE, wg_cfg)

        # ── 自动生成第一个客户端 ─────────────────────────────────────────────
        sp.step(t("wireguard.step.gen_client"))
        client_priv, client_pub = gen_wg_keypair()
        client_psk = gen_wg_psk()
        client_ip = f"10.10.{_octet3}.{VPN_CLIENT_IP_START}"

        # 追加 peer 到 wg0.conf
        peer_section = wg_peer_section(
            client_public_key=client_pub,
            client_ip=client_ip,
            psk=client_psk,
        )
        from pathlib import Path as _AppendPath
        with open(WG_CONFIG_FILE, "a") as f:
            f.write(peer_section)

        # 提取基准域名（如 wg.icerror.top → icerror.top）
        _sni_parts = sni.strip().split(".")
        _base_domain = ".".join(_sni_parts[-2:]) if len(_sni_parts) >= 2 else sni

        # 生成令牌（含 dns 字段）
        token = _generate_client_token(
            server_ip=public_ip,
            server_port=server_port,
            wg_server_pub=wg_server_pub,
            reality_pub=xray_pub,
            uuid=uuid,
            short_id=short_id,
            sni=sni,
            client_ip=client_ip,
            wg_client_priv=client_priv,
            wg_psk=client_psk,
            vpn_subnet=vpn_subnet,
            vpn_gateway=vpn_gateway,
            label=tunnel_label,
            dns=vpn_gateway,
        )

        # 保存状态（令牌生成后写入，确保 client-1 含 token 字段）
        _save_state({
            "mode": "443",
            "sni": sni,
            "base_domain": _base_domain,
            "server_ip": public_ip,
            "server_port": server_port,
            "uuid": uuid,
            "short_id": short_id,
            "xray_pub": xray_pub,
            "wg_server_pub": wg_server_pub,
            "vpn_subnet": vpn_subnet,
            "vpn_gateway": vpn_gateway,
            "tunnel_label": tunnel_label,
            "clients": [
                {
                    "name": "client-1",
                    "ip": client_ip,
                    "pubkey": client_pub,
                    "token": token,
                }
            ],
        })

        # ── 安装 dnsmasq ──────────────────────────────────────────────────────
        sp.step(t("wireguard.step.install_dnsmasq"))
        _setup_dnsmasq(vpn_gateway=vpn_gateway, base_domain=_base_domain, os_id=os_id)

        # ── 启动服务 ─────────────────────────────────────────────────────────────
        sp.step(t("wireguard.step.start_services"))
        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True,
                       capture_output=True, text=True)
        from pathlib import Path as _SysPath
        _SysPath("/etc/sysctl.d/99-wg.conf").write_text("net.ipv4.ip_forward=1\n")

        from wireguard.utils import stop_and_disable as _sad
        _sad(XRAY_SERVICE)
        _sad(WG_SERVICE)
        enable_and_start(XRAY_SERVICE)
        enable_and_start(WG_SERVICE)
        subprocess.run(["nginx", "-t"], check=True, capture_output=True, text=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=False,
                       capture_output=True, text=True)
        SysConfigManager.mark_installed("wg_server")

        # ── 验证 ─────────────────────────────────────────────────────────────
        sp.step(t("wireguard.step.verify"))
        import time as _time
        _time.sleep(2)
        from wireguard.utils import is_service_active
        _xray_ok = is_service_active(XRAY_SERVICE)
        _wg_ok   = is_service_active(WG_SERVICE)
        _r_nginx = subprocess.run(
            ["systemctl", "is-active", "nginx"],
            capture_output=True, text=True, check=False,
        )
        _nginx_ok = _r_nginx.stdout.strip() == "active"
        if not (_xray_ok and _wg_ok and _nginx_ok):
            raise InstallError(t(
                "wireguard.error.service_start_fail",
                xray='OK' if _xray_ok else 'FAIL',
                wg='OK' if _wg_ok else 'FAIL',
                nginx='OK' if _nginx_ok else 'FAIL',
            ))

    # ── 输出结果 ─────────────────────────────────────────────────────────────
    clear_screen()
    print_action_title(breadcrumb)
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text as _Text

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)
    info_rows = [
        (t("wireguard.info.public_ip"),   public_ip),
        (t("wireguard.info.mode"),        t("wireguard.mode_443")),
        (t("wireguard.info.xray_port"),   str(server_port)),
        (t("wireguard.info.vpn_gateway"), vpn_gateway),
        (t("wireguard.info.dns"),         vpn_gateway),
    ]
    info_rows.insert(1, (t("wireguard.info.domain"), sni))
    for label, value in info_rows:
        tbl.add_row(_Text(label, style="#7f849c"), _Text(value, style="bold #cdd6f4"))
    console.print(Panel(
        tbl,
        title=f"[bold #a6e3a1]{t('wireguard.install_server_success')}[/bold #a6e3a1]",
        border_style="#a6e3a1",
        padding=(1, 2),
    ))
    console.print()

    # ── 令牌输出 ─────────────────────────────────────────────────────────────
    console.print(f"[bold #f9e2af]▶  {t('wireguard.token_panel_title')}[/bold #f9e2af]")
    console.print(f"[#cdd6f4]{t('wireguard.token_hint')}[/#cdd6f4]")
    console.print()
    import sys as _sys_token
    _sys_token.stdout.write(token + "\n")
    _sys_token.stdout.flush()
    console.print()

    # ── 手机 vless:// 二维码 ─────────────────────────────────────────────────────────
    from wireguard.constants import XRAY_WS_PATH
    _vless_uri = _build_vless_uri(
        uuid=uuid, sni=sni, server_ip=public_ip, port=server_port,
        ws_path=XRAY_WS_PATH, label=tunnel_label,
    )
    console.print(f"[bold #89b4fa]▶  {t('wireguard.phone_vless_title')}[/bold #89b4fa]")
    console.print(f"[#6c7086]{t('wireguard.phone_vless_hint')}[/#6c7086]")
    console.print()
    _print_qr(_vless_uri)
    console.print()

    # ── 防火墙提示 ────────────────────────────────────────────────────────────────────
    _fw_tbl = Table.grid(padding=(0, 2))
    _fw_tbl.add_column(no_wrap=False)
    _fw_tbl.add_row(_Text(t("wireguard.firewall_hint"), style="#cdd6f4"))
    _fw_tbl.add_row(_Text(t("wireguard.firewall_tcp_rule").format(port=server_port), style="bold #a6e3a1"))
    _fw_tbl.add_row(_Text(t("wireguard.firewall_udp_note").format(port=wg_port), style="#6c7086"))
    console.print(Panel(
        _fw_tbl,
        title=f"[bold #f9e2af]⚠  {t('wireguard.firewall_panel_title')}[/bold #f9e2af]",
        border_style="#f9e2af",
        padding=(1, 2),
    ))
    pause()


def uninstall_server() -> None:
    """公网服务端卸载"""
    from core.i18n import t
    from core.theme import print_success, print_action_title
    from core.progress import MultiStepProgress
    from wireguard.constants import (
        WG_SERVICE, XRAY_SERVICE,
        WG_CONFIG_DIR, XRAY_CONFIG_DIR,
        XRAY_BINARY,
        NGINX_VLESS_WS_CONF, NGINX_STREAM_CONF, NGINX_STEAL_CONF,
    )
    from wireguard.utils import stop_and_disable

    import subprocess
    import shutil
    from pathlib import Path

    step_keys = [
        "wireguard.step.uninstall_stop_wg",
        "wireguard.step.uninstall_stop_xray",
        "wireguard.step.uninstall_clean_services",
        "wireguard.step.uninstall_clean_config",
        "wireguard.step.uninstall_clean_logs",
        "wireguard.step.uninstall_clean_system",
    ]
    descs = [t(k) for k in step_keys]

    print_action_title(["OpsKit", t("menu.software"), t("software.wireguard"), t("software.uninstall")])
    with MultiStepProgress(descs) as sp:
        # ── 1. 停止 WireGuard ────────────────────────────────────────────────
        sp.step(descs[0])
        subprocess.run(["wg-quick", "down", "wg0"], check=False,
                       capture_output=True, text=True)
        stop_and_disable(WG_SERVICE)

        # ── 2. 停止 xray 及 xray-restart.timer ──────────────────────────────
        sp.step(descs[1])
        stop_and_disable("xray-restart.timer")
        stop_and_disable(XRAY_SERVICE)

        # ── 3. 清理 systemd service 文件 ─────────────────────────────────────
        sp.step(descs[2])
        for svc in (XRAY_SERVICE,):
            svc_path = Path(f"/etc/systemd/system/{svc}.service")
            svc_path.unlink(missing_ok=True)
            override_dir = Path(f"/etc/systemd/system/{svc}.service.d")
            shutil.rmtree(override_dir, ignore_errors=True)
        for tpl in ("xray@",):
            Path(f"/etc/systemd/system/{tpl}.service").unlink(missing_ok=True)
            shutil.rmtree(f"/etc/systemd/system/{tpl}.service.d", ignore_errors=True)
        Path("/etc/systemd/system/xray-restart.timer").unlink(missing_ok=True)
        Path("/etc/systemd/system/xray-restart.service").unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        Path(XRAY_BINARY).unlink(missing_ok=True)

        # ── 4. 清理配置目录 ───────────────────────────────────────────────────
        sp.step(descs[3])
        for path in (WG_CONFIG_DIR, XRAY_CONFIG_DIR):
            shutil.rmtree(path, ignore_errors=True)
        Path(NGINX_VLESS_WS_CONF).unlink(missing_ok=True)
        Path(NGINX_STREAM_CONF).unlink(missing_ok=True)
        Path(NGINX_STEAL_CONF).unlink(missing_ok=True)
        subprocess.run(["nginx", "-t"], check=False, capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=False, capture_output=True)

        # ── 5. 清理日志目录 ───────────────────────────────────────────────────
        sp.step(descs[4])
        from core.paths import xray_log_dir, xray_data_dir
        for path in (xray_log_dir(), xray_data_dir()):
            shutil.rmtree(str(path), ignore_errors=True)

        # ── 6. 清理系统配置 ──────────────────────────────────────────────────────
        sp.step(descs[5])
        Path("/etc/sysctl.d/99-wg.conf").unlink(missing_ok=True)
        from wireguard.constants import DNSMASQ_CONF_PATH as _DNSMASQ_CONF
        Path(_DNSMASQ_CONF).unlink(missing_ok=True)
        from core.sysconfig import SysConfigManager as _SCM
        _SCM.restore("wg_server")
        _SCM.remove("wg_server")
        Path("/root/wg_server_info.txt").unlink(missing_ok=True)

    print_success(t('wireguard.diagnose.uninstall_success'))


def diagnose_server() -> None:
    """公网服务端诊断"""
    from core.i18n import t as _t
    from core.theme import print_action_title
    from core.prompt import pause
    print_action_title(["OpsKit", _t("menu.software"), _t("software.wireguard"), _t("software.diagnose")])
    import json
    import subprocess
    from pathlib import Path
    from rich.panel import Panel
    from rich.table import Table
    from rich.rule import Rule
    from rich.text import Text
    from core.i18n import t
    from wireguard.constants import (
        WG_SERVICE, XRAY_SERVICE,
        WG_CONFIG_FILE, XRAY_CONFIG_FILE,
    )
    from wireguard.utils import is_service_active, is_port_listening

    dk = "wireguard.diagnose"

    def _dot(ok: bool) -> Text:
        return Text("● active", style="#a6e3a1") if ok else Text("● inactive", style="#f38ba8")

    def _trunc(s: str, n: int = 42) -> str:
        return s if len(s) <= n else s[:n] + "..."

    def _fmt_handshake(seconds: int) -> str:
        if seconds <= 0:
            return "—"
        if seconds < 60:
            return f"{seconds}s 前"
        if seconds < 3600:
            return f"{seconds // 60}分钟前"
        if seconds < 86400:
            return f"{seconds // 3600}小时前"
        return f"{seconds // 86400}天前"

    # ── 构建内容表格 ───────────────────────────────────────────────────────────
    _LABEL = "#7f849c"   # 标签色：柔和灰，不刺眼但可读
    _VALUE = "bold #cdd6f4"  # 值色：亮白加粗
    _SEC   = "bold #89b4fa"  # 分区标题：蓝色加粗

    def _lbl(s: str) -> Text:
        return Text(s, style=_LABEL)

    def _val(s: str) -> Text:
        return Text(s, style=_VALUE)

    def _sec(s: str) -> Text:
        return Text(f"── {s} ──", style=_SEC)

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)

    # ── 1. 服务状态 ────────────────────────────────────────────────────────────
    tbl.add_row(_sec(t(f'{dk}.section_services')), Text(""))
    wg_ok = is_service_active(WG_SERVICE)
    xray_ok = is_service_active(XRAY_SERVICE)
    tbl.add_row(_lbl(t(f"{dk}.wg_service")), _dot(wg_ok))
    tbl.add_row(_lbl(t(f"{dk}.xray_service")), _dot(xray_ok))
    tbl.add_row(Text(""), Text(""))

    # ── 2. 连接参数（从配置文件读取）─────────────────────────────────────────
    tbl.add_row(_sec(t(f'{dk}.section_config')), Text(""))
    wg_pub = "—"
    wg_port = "—"
    xray_port = "—"
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
            if line.startswith("ListenPort"):
                wg_port = line.split("=", 1)[1].strip() if "=" in line else "—"
    except Exception:
        pass
    try:
        cfg = json.loads(Path(XRAY_CONFIG_FILE).read_text())
        inbounds = cfg.get("inbounds", [])
        for ib in inbounds:
            if ib.get("protocol") in ("vless", "vmess", "reality") or ib.get("streamSettings", {}).get("security") == "reality":
                xray_port = str(ib.get("port", "—"))
                break
        if xray_port == "—" and inbounds:
            xray_port = str(inbounds[0].get("port", "—"))
    except Exception:
        pass
    def _port_status(port_val: str, proto: str) -> Text:
        try:
            p = int(port_val)
            ok = is_port_listening(p, proto)
        except (ValueError, Exception):
            ok = False
        label = t(f"{dk}.port_listening") if ok else t(f"{dk}.port_not_listening")
        color = "#a6e3a1" if ok else "#f38ba8"
        return Text(f"{port_val}  {label}", style=f"bold {color}")

    tbl.add_row(_lbl(t(f"{dk}.wg_pub")), _val(wg_pub))
    tbl.add_row(_lbl(t(f"{dk}.wg_port")), _port_status(wg_port, "udp"))
    tbl.add_row(_lbl(t(f"{dk}.xray_port")), _port_status(xray_port, "tcp"))
    from core.sysconfig import _load as _sc_load
    _srv_state = _sc_load().get("wg_server", {})
    _vpn_gw = _srv_state.get("vpn_gateway", "—")
    tbl.add_row(_lbl(t(f"{dk}.vpn_gateway")), _val(_vpn_gw))
    tbl.add_row(_lbl(t("wireguard.info.dns")), _val(_vpn_gw if _vpn_gw != "—" else "—"))
    tbl.add_row(Text(""), Text(""))

    # ── 3. 客户端连接凭证（从 xray config 读取）──────────────────────────────
    tbl.add_row(_sec(t(f'{dk}.section_client_creds')), Text(""))
    creds_xray_pub = "—"
    creds_uuid = "—"
    creds_short_id = "—"
    try:
        cfg = json.loads(Path(XRAY_CONFIG_FILE).read_text())
        ib = cfg["inbounds"][0]
        priv_key = ib["streamSettings"]["realitySettings"]["privateKey"]
        r = subprocess.run(
            ["xray", "x25519", "-i", priv_key],
            capture_output=True, text=True, check=False
        )
        for line in r.stdout.splitlines():
            if "Public" in line:
                creds_xray_pub = line.split(":", 1)[-1].strip()
                break
        creds_uuid = ib["settings"]["clients"][0]["id"]
        creds_short_id = ib["streamSettings"]["realitySettings"]["shortIds"][0]
    except Exception:
        pass
    tbl.add_row(_lbl(t(f"{dk}.creds_xray_pub")), _val(creds_xray_pub))
    tbl.add_row(_lbl(t(f"{dk}.creds_uuid")),     _val(creds_uuid))
    tbl.add_row(_lbl(t(f"{dk}.creds_short_id")), _val(creds_short_id))
    tbl.add_row(Text(""), Text(""))

    # ── 4. peer 统计（wg show wg0 dump）──────────────────────────────────────
    tbl.add_row(_sec(t(f'{dk}.section_peers')), Text(""))
    peers: list[dict] = []
    try:
        result = subprocess.run(
            ["wg", "show", "wg0", "dump"],
            capture_output=True, text=True, check=False,
        )
        lines = result.stdout.strip().splitlines()
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) >= 7:
                peers.append({
                    "pubkey":    parts[0],
                    "endpoint":  parts[2] if parts[2] != "(none)" else "—",
                    "allowed":   parts[3],
                    "handshake": int(parts[4]) if parts[4].isdigit() else 0,
                    "rx":        int(parts[5]) if parts[5].isdigit() else 0,
                    "tx":        int(parts[6]) if parts[6].isdigit() else 0,
                    "keepalive": parts[7] if len(parts) > 7 and parts[7] != "off" else "—",
                })
    except Exception:
        pass

    def _fmt_bytes(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 ** 2:
            return f"{n / 1024:.1f} KiB"
        if n < 1024 ** 3:
            return f"{n / 1024 ** 2:.2f} MiB"
        return f"{n / 1024 ** 3:.2f} GiB"

    if not peers:
        tbl.add_row(Text(""), Text(t(f"{dk}.no_peers"), style="dim #6c7086"))
    else:
        for i, p in enumerate(peers):
            if i > 0:
                tbl.add_row(Text(""), Text(""))
            tbl.add_row(_lbl(t(f"{dk}.peer_pubkey")),   _val(_trunc(p["pubkey"])))
            tbl.add_row(_lbl(t(f"{dk}.peer_endpoint")),  _val(p["endpoint"]))
            tbl.add_row(_lbl(t(f"{dk}.peer_handshake")), _val(_fmt_handshake(p["handshake"])))
            tbl.add_row(_lbl(t(f"{dk}.peer_rx")),        _val(_fmt_bytes(p["rx"])))
            tbl.add_row(_lbl(t(f"{dk}.peer_tx")),        _val(_fmt_bytes(p["tx"])))
            if p["keepalive"] != "—":
                tbl.add_row(_lbl(t(f"{dk}.peer_keepalive")), _val(f"{p['keepalive']}s"))

    console.print(Panel(
        tbl,
        title=f"[bold]{t(f'{dk}.title')}[/bold]",
        border_style="#89b4fa",
        padding=(1, 2),
    ))
    pause()


# ─── peer 管理 ────────────────────────────────────────────────────────────────

def _get_peers() -> list[dict]:
    """从 wg show wg0 dump 解析所有 peer 信息"""
    import subprocess
    peers: list[dict] = []
    try:
        result = subprocess.run(
            ["wg", "show", "wg0", "dump"],
            capture_output=True, text=True, check=False,
        )
        lines = result.stdout.strip().splitlines()
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) >= 7:
                peers.append({
                    "pubkey":  parts[0],
                    "allowed": parts[3],
                    "handshake": int(parts[4]) if parts[4].isdigit() else 0,
                    "rx":      int(parts[5]) if parts[5].isdigit() else 0,
                    "tx":      int(parts[6]) if parts[6].isdigit() else 0,
                })
    except Exception:
        pass
    return peers


def _next_peer_ip(peers: list[dict], octet3: int = 10) -> str:
    """根据已有 peer 的 allowed-ips 推算下一个可用 10.10.{octet3}.x/32"""
    from wireguard.constants import VPN_PEER_IP_START
    prefix = f"10.10.{octet3}."
    used: set[int] = set()
    for p in peers:
        for cidr in p["allowed"].split(","):
            cidr = cidr.strip()
            if cidr.startswith(prefix):
                try:
                    host = int(cidr.split("/")[0].split(".")[-1])
                    used.add(host)
                except ValueError:
                    pass
    candidate = VPN_PEER_IP_START
    while candidate in used:
        candidate += 1
    return f"{prefix}{candidate}/32"


def _fmt_bytes(n: int) -> str:
    """格式化字节数"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.2f} MiB"
    return f"{n / 1024 ** 3:.2f} GiB"


def _fmt_handshake(seconds: int) -> str:
    """格式化握手时间"""
    if seconds <= 0:
        return "—"
    import time
    ago = int(time.time()) - seconds
    if ago < 0:
        ago = 0
    if ago < 60:
        return f"{ago}s"
    if ago < 3600:
        return f"{ago // 60}m"
    if ago < 86400:
        return f"{ago // 3600}h"
    return f"{ago // 86400}d"


def _trunc(s: str, n: int = 42) -> str:
    return s if len(s) <= n else s[:n] + "..."


def _alloc_client_ip(clients: list[dict]) -> int | None:
    """用整数位图找最小可用 IP suffix。

    算法：
    1. 遍历 clients 一次（O(n)），将已用 suffix 写入 int 位图
    2. 在合法范围内用位运算 (~used & range_mask) 求最低空闲位
    3. (x & -x).bit_length() - 1 在 O(1) 内定位最低置 1 位

    总复杂度 O(n)，无排序，无二次遍历。
    """
    from wireguard.constants import VPN_CLIENT_IP_START, VPN_CLIENT_IP_MAX
    used = 0
    for c in clients:
        try:
            suffix = int(c.get("ip", "").rsplit(".", 1)[-1])
        except (ValueError, IndexError):
            continue
        if VPN_CLIENT_IP_START <= suffix <= VPN_CLIENT_IP_MAX:
            used |= (1 << suffix)
    lo, hi = VPN_CLIENT_IP_START, VPN_CLIENT_IP_MAX
    range_mask = ((1 << (hi + 1)) - 1) ^ ((1 << lo) - 1)
    free = (~used) & range_mask
    if not free:
        return None
    return (free & -free).bit_length() - 1


def _get_clients() -> list[dict]:
    """从 state.clients 返回已注册客户端列表"""
    return _load_state().get("clients", [])


def _has_icon(key: str) -> bool:
    """检查主题是否定义了指定 icon key"""
    try:
        from core.theme import get_icon
        val = get_icon(key)
        return bool(val and val != key)
    except Exception:
        return False


def manage_peers() -> None:
    """peer 管理主菜单"""
    from core.i18n import t
    from core.prompt import select, UserCancel, pause
    from core.theme import get_icon, get_color, print_action_title, print_warning

    mk = "wireguard.manage"
    breadcrumb = ["OpsKit", t("menu.software"), t("software.wireguard"),
                  t("software.wg_server"), t(f"{mk}.title")]
    muted = get_color("muted")

    while True:
        installed = bool(_load_state())

        if installed:
            choices = [
                {"key": "1", "label": f"{get_icon('add')} {t(f'{mk}.add_peer')}"},
                {"key": "2", "label": f"{get_icon('list')} {t(f'{mk}.list_peers')}"},
                {"key": "3", "label": f"{get_icon('edit')} {t(f'{mk}.rename_peer')}"},
                {"key": "4", "label": f"{get_icon('delete')} {t(f'{mk}.remove_peer')}"},
                {"key": "5", "label": f"{get_icon('network')} {t(f'{mk}.setup_dns')}"},
            ]
        else:
            choices = [
                {"key": "1", "label": f"[{muted}]{get_icon('add')} {t(f'{mk}.add_peer')}[/{muted}]", "disabled": True},
                {"key": "2", "label": f"{get_icon('list')} {t(f'{mk}.list_peers')}"},
                {"key": "3", "label": f"[{muted}]{get_icon('edit')} {t(f'{mk}.rename_peer')}[/{muted}]", "disabled": True},
                {"key": "4", "label": f"[{muted}]{get_icon('delete')} {t(f'{mk}.remove_peer')}[/{muted}]", "disabled": True},
                {"key": "5", "label": f"[{muted}]{get_icon('network')} {t(f'{mk}.setup_dns')}[/{muted}]", "disabled": True},
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

        if key == "1":
            if not installed:
                print_warning(t("wireguard.manage.not_installed"))
                pause()
            else:
                add_peer(breadcrumb)
        elif key == "2":
            list_peers(breadcrumb)
        elif key == "3":
            if not installed:
                print_warning(t("wireguard.manage.not_installed"))
                pause()
            else:
                rename_peer(breadcrumb)
        elif key == "4":
            if not installed:
                print_warning(t("wireguard.manage.not_installed"))
                pause()
            else:
                remove_peer(breadcrumb)
        elif key == "5":
            if not installed:
                print_warning(t("wireguard.manage.not_installed"))
                pause()
            else:
                _run_setup_dns(breadcrumb)


def _run_setup_dns(breadcrumb: list[str]) -> None:
    """补装/更新 dnsmasq DNS 配置（已安装服务端使用）"""
    from core.i18n import t
    from core.prompt import pause, clear_screen
    from core.theme import print_action_title, print_success
    from wireguard.utils import get_os_id

    clear_screen()
    mk = "wireguard.manage"
    print_action_title([*breadcrumb, t(f"{mk}.setup_dns")])

    state = _load_state()
    vpn_gateway  = state.get("vpn_gateway", "")
    base_domain  = state.get("base_domain", "")
    if not vpn_gateway or not base_domain:
        sni = state.get("sni", "")
        _parts = sni.strip().split(".")
        base_domain = ".".join(_parts[-2:]) if len(_parts) >= 2 else sni

    os_id = get_os_id()
    _setup_dnsmasq(vpn_gateway=vpn_gateway, base_domain=base_domain, os_id=os_id)
    print_success(t("wireguard.dnsmasq_setup_success").format(domain=base_domain, dns=vpn_gateway))
    pause()


def add_peer(breadcrumb: list[str]) -> None:
    """添加客户端 peer — 自动生成密钥并输出连接令牌"""
    import subprocess
    from core.i18n import t
    from core.prompt import pause, clear_screen, text_input, UserCancel
    from core.theme import print_error, print_action_title
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from wireguard.utils import gen_wg_keypair, gen_wg_psk
    from wireguard.constants import VPN_CLIENT_IP_MAX

    mk = "wireguard.manage"
    sub = [*breadcrumb, t(f"{mk}.add_peer")]

    state = _load_state()
    if not state:
        print_error(t(f"{mk}.no_state"))
        pause()
        return

    next_ip_num = _alloc_client_ip(state.get("clients", []))
    if next_ip_num is None:
        print_error(t(f"{mk}.max_clients"))
        pause()
        return

    # ── 输入客户端名称（有默认值）──────────────────────────────────────────────
    default_name = f"client-{len(state.get('clients', [])) + 1}"
    try:
        raw = text_input(
            breadcrumb=sub,
            prompt=t(f"{mk}.input_peer_name"),
            hint=default_name,
            default=default_name,
            theme_key="software",
        )
    except UserCancel:
        return
    client_name = raw.strip() if raw and raw.strip() else default_name

    _vpn_subnet  = state.get("vpn_subnet",  "10.10.10.0/24")
    _vpn_gateway = state.get("vpn_gateway", "10.10.10.1")
    _tunnel_label = state.get("tunnel_label", "default")
    _octet3 = int(_vpn_gateway.split(".")[2]) if _vpn_gateway else 10
    client_ip = f"10.10.{_octet3}.{next_ip_num}"
    client_priv, client_pub = gen_wg_keypair()
    client_psk = gen_wg_psk()

    # 添加 peer 到运行中的 wg0
    subprocess.run(
        ["wg", "set", "wg0", "peer", client_pub,
         "preshared-key", "/dev/stdin",
         "allowed-ips", f"{client_ip}/32"],
        input=client_psk,
        check=False, capture_output=True, text=True,
    )
    subprocess.run(
        ["wg-quick", "save", "wg0"],
        check=False, capture_output=True, text=True,
    )

    # 生成令牌（含 dns 字段）
    token = _generate_client_token(
        server_ip=state.get("server_ip", ""),
        server_port=state.get("server_port", 443),
        wg_server_pub=state.get("wg_server_pub", ""),
        reality_pub=state.get("xray_pub", ""),
        uuid=state.get("uuid", ""),
        short_id=state.get("short_id", ""),
        sni=state.get("sni", ""),
        client_ip=client_ip,
        wg_client_priv=client_priv,
        wg_psk=client_psk,
        vpn_subnet=_vpn_subnet,
        vpn_gateway=_vpn_gateway,
        label=_tunnel_label,
        dns=_vpn_gateway,
    )

    # 更新状态（含令牌）
    clients = state.get("clients", [])
    clients.append({"name": client_name, "ip": client_ip,
                    "pubkey": client_pub, "token": token})
    state["clients"] = clients
    _save_state(state)

    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"

    clear_screen()
    print_action_title(sub)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)
    tbl.add_row(Text(t(f"{mk}.peer_name"), style=_LABEL),
                Text(client_name, style=_VALUE))
    tbl.add_row(Text(t(f"{mk}.peer_ip"), style=_LABEL),
                Text(client_ip, style=_VALUE))
    console.print(Panel(
        tbl,
        title=f"[bold #a6e3a1]{t(f'{mk}.peer_added')}[/bold #a6e3a1]",
        border_style="#a6e3a1",
        padding=(1, 2),
    ))
    console.print()

    # 输出令牌
    console.print(f"[bold #f9e2af]▶  {t('wireguard.token_panel_title')}[/bold #f9e2af]")
    console.print(f"[#cdd6f4]{t('wireguard.token_hint')}[/#cdd6f4]")
    console.print()
    import sys as _sys_token2
    _sys_token2.stdout.write(token + "\n")
    _sys_token2.stdout.flush()
    console.print()

    # ── 手机 vless:// 二维码 ──────────────────────────────────────────────
    from wireguard.constants import XRAY_WS_PATH as _WS_PATH
    _vless_uri = _build_vless_uri(
        uuid=state.get("uuid", ""),
        sni=state.get("sni", ""),
        server_ip=state.get("server_ip", ""),
        port=state.get("server_port", 443),
        ws_path=_WS_PATH,
        label=state.get("tunnel_label", ""),
    )
    console.print(f"[bold #89b4fa]▶  {t('wireguard.phone_vless_title')}[/bold #89b4fa]")
    console.print(f"[#6c7086]{t('wireguard.phone_vless_hint')}[/#6c7086]")
    console.print()
    _print_qr(_vless_uri)
    console.print()
    pause()


def list_peers(breadcrumb: list[str]) -> None:
    """客户端列表"""
    from core.i18n import t
    from core.prompt import pause, clear_screen
    from core.theme import print_action_title
    from rich.table import Table as RichTable
    from rich import box as rich_box

    mk = "wireguard.manage"
    sub = [*breadcrumb, t(f"{mk}.list_peers")]

    clear_screen()
    print_action_title(sub)

    clients = _get_clients()
    tbl = RichTable(
        box=rich_box.ROUNDED,
        border_style="#89b4fa",
        header_style="bold #89b4fa",
        show_header=True,
        padding=(0, 1),
    )
    tbl.add_column(t(f"{mk}.peer_ip"),    style="#cdd6f4",   no_wrap=True,  min_width=14)
    tbl.add_column(t(f"{mk}.peer_name"),  style="bold #cdd6f4", no_wrap=True, min_width=12)
    tbl.add_column(t(f"{mk}.peer_token"), style="#6c7086",   no_wrap=True,  min_width=24)

    if not clients:
        tbl.add_row("", t(f"{mk}.no_clients"), "")
    else:
        for c in clients:
            tbl.add_row(
                c.get("ip", "—"),
                c.get("name", "—"),
                _trunc(c.get("token", ""), 28),
            )

    console.print(tbl)
    pause()


def remove_peer(breadcrumb: list[str]) -> None:
    """删除客户端 peer"""
    import subprocess
    from core.i18n import t
    from core.prompt import select, pause, clear_screen, UserCancel
    from core.theme import get_icon, print_action_title, print_warning
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    mk = "wireguard.manage"
    sub = [*breadcrumb, t(f"{mk}.remove_peer")]

    clients = _get_clients()
    if not clients:
        print_warning(t(f"{mk}.no_clients"))
        pause()
        return

    choices = [
        {
            "key": str(i + 1),
            "label": f"{c.get('ip', '?'):<16} {c.get('name', '?'):<16} {_trunc(c.get('token', ''), 20)}",
        }
        for i, c in enumerate(clients)
    ]
    try:
        key = select(
            breadcrumb=sub,
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key="software",
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if key is None:
        return

    idx = int(key) - 1
    client = clients[idx]
    pubkey = client.get("pubkey", "")

    if pubkey:
        subprocess.run(
            ["wg", "set", "wg0", "peer", pubkey, "remove"],
            check=False, capture_output=True, text=True,
        )
        subprocess.run(
            ["wg-quick", "save", "wg0"],
            check=False, capture_output=True, text=True,
        )

    # 从 state.clients 移除
    state = _load_state()
    state["clients"] = [c for c in state.get("clients", []) if c.get("pubkey") != pubkey]
    _save_state(state)

    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"

    clear_screen()
    print_action_title(sub)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)
    tbl.add_row(Text(t(f"{mk}.peer_ip"), style=_LABEL),
                Text(client.get("ip", "—"), style=_VALUE))
    tbl.add_row(Text(t(f"{mk}.peer_name"), style=_LABEL),
                Text(client.get("name", "—"), style=_VALUE))
    console.print(Panel(
        tbl,
        title=f"[bold #a6e3a1]{t(f'{mk}.peer_removed')}[/bold #a6e3a1]",
        border_style="#a6e3a1",
        padding=(1, 2),
    ))
    pause()


def rename_peer(breadcrumb: list[str]) -> None:
    """修改客户端名称"""
    from core.i18n import t
    from core.prompt import select, pause, clear_screen, text_input, UserCancel
    from core.theme import get_icon, print_action_title, print_warning
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    mk = "wireguard.manage"
    sub = [*breadcrumb, t(f"{mk}.rename_peer")]

    clients = _get_clients()
    if not clients:
        print_warning(t(f"{mk}.no_clients"))
        pause()
        return

    choices = [
        {
            "key": str(i + 1),
            "label": f"{c.get('ip', '?'):<16} {c.get('name', '?'):<16} {_trunc(c.get('token', ''), 20)}",
        }
        for i, c in enumerate(clients)
    ]
    try:
        key = select(
            breadcrumb=sub,
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key="software",
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if key is None:
        return

    idx = int(key) - 1
    client = clients[idx]
    old_name = client.get("name", "")

    try:
        raw = text_input(
            breadcrumb=sub,
            prompt=t(f"{mk}.input_peer_name"),
            hint=old_name,
            default=old_name,
            theme_key="software",
        )
    except UserCancel:
        return
    new_name = raw.strip() if raw and raw.strip() else old_name

    if new_name == old_name:
        return

    state = _load_state()
    for c in state.get("clients", []):
        if c.get("pubkey") == client.get("pubkey"):
            c["name"] = new_name
            break
    _save_state(state)

    _LABEL = "#7f849c"
    _VALUE = "bold #cdd6f4"

    clear_screen()
    print_action_title(sub)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=False)
    tbl.add_row(Text(t(f"{mk}.peer_ip"), style=_LABEL),
                Text(client.get("ip", "—"), style=_VALUE))
    tbl.add_row(Text(t(f"{mk}.peer_name"), style=_LABEL),
                Text(new_name, style=_VALUE))
    console.print(Panel(
        tbl,
        title=f"[bold #a6e3a1]{t(f'{mk}.peer_renamed')}[/bold #a6e3a1]",
        border_style="#a6e3a1",
        padding=(1, 2),
    ))
    pause()


# ─── 内部辅助 ─────────────────────────────────────────────────────────────────


def _ensure_system_deps(os_id: str) -> None:
    """检测并自动安装 WireGuard 服务端所需的前置系统依赖。
    缺失的包静默安装，已安装则跳过，不影响进度条显示。
    """
    import shutil
    from core.pkg_runner import get_runner

    _check_map: dict[str, str]
    if os_id in ("debian", "ubuntu"):
        _check_map = {
            "iptables":  "iptables",
            "curl":      "curl",
            "openssl":   "openssl",
            "iproute2":  "ip",
        }
    else:
        _check_map = {
            "iptables": "iptables",
            "curl":     "curl",
            "openssl":  "openssl",
            "iproute":  "ip",
        }

    missing = [pkg for pkg, cmd in _check_map.items() if not shutil.which(cmd)]
    if missing:
        runner = get_runner()
        runner.update_index()
        runner.install(missing)


def _get_acme_email() -> str:
    """获取 ACME 邮箱：优先读 config，否则随机生成 @gmail.com 并持久化"""
    import uuid
    from core.config import set_config_value, load_config
    cfg = load_config()
    saved = (cfg.get('wireguard') or {}).get('acme_email', '')
    if saved and '@' in saved and not saved.endswith('.local'):
        return saved
    email = f'opskit.{uuid.uuid4().hex[:12]}@gmail.com'
    set_config_value(cfg, 'wireguard.acme_email', email)
    return email


def _check_port_443() -> tuple:
    """检测 443 端口占用情况。
    返回 ("free"|"nginx"|"other", 占用进程描述字符串)
    """
    import subprocess
    import shutil
    result = ("free", "")
    try:
        r = subprocess.run(
            ["ss", "-tlnp", "sport", "=", ":443"],
            capture_output=True, text=True, check=False,
        )
        lines = r.stdout.strip().splitlines()
        for line in lines[1:]:
            if ":443" not in line:
                continue
            proc_desc = ""
            if "users:" in line:
                proc_desc = line[line.index("users:"):].strip()
            if "nginx" in line.lower():
                return ("nginx", proc_desc)
            return ("other", proc_desc)
    except Exception:
        pass
    if shutil.which("lsof"):
        try:
            r2 = subprocess.run(
                ["lsof", "-i", ":443", "-sTCP:LISTEN", "-n", "-P"],
                capture_output=True, text=True, check=False,
            )
            lines2 = r2.stdout.strip().splitlines()
            for line in lines2[1:]:
                cols = line.split()
                if not cols:
                    continue
                proc_name = cols[0].lower()
                proc_desc = " ".join(cols[:3])
                if "nginx" in proc_name:
                    return ("nginx", proc_desc)
                return ("other", proc_desc)
        except Exception:
            pass
    return result


def _setup_nginx(os_id: str, nginx_mode: str, sni: str, ws_port: int, cert_dir: str, ws_path: str, sp=None) -> None:
    """根据场景初始化 nginx 配置。
    nginx_mode:
      "install" — 无 nginx，复用 NginxRecipe._do_install 安装并删除默认站点
      "append"  — 已有 nginx，只追加本程序配置文件
    两种模式都只写 NGINX_VLESS_WS_CONF，不改用户其他文件。
    """
    import subprocess
    from pathlib import Path
    from wireguard.constants import (
        NGINX_VLESS_WS_CONF, NGINX_STREAM_CONF, NGINX_STEAL_CONF,
    )
    from wireguard.templates import nginx_http_only_config
    from wireguard.utils import write_file

    if nginx_mode == "install":
        from software.recipes.nginx.recipe import NginxRecipe
        NginxRecipe()._do_install(on_progress=sp.set_step_pct if sp else None)
        from core.paths import nginx_sites_enabled_dir, nginx_conf_dir
        (nginx_sites_enabled_dir() / "default").unlink(missing_ok=True)
        (nginx_conf_dir() / "default.conf").unlink(missing_ok=True)

    for _old_conf in (NGINX_VLESS_WS_CONF, NGINX_STREAM_CONF, NGINX_STEAL_CONF):
        Path(_old_conf).unlink(missing_ok=True)

    write_file(NGINX_VLESS_WS_CONF, nginx_http_only_config(sni))
    subprocess.run(["nginx", "-t"], check=True, capture_output=True, text=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=False, capture_output=True, text=True)


def _issue_cert(sni: str, email: str, cert_dir: str) -> None:
    """通过 acme.sh 申请并安装 TLS 证书（国内镜像优先）"""
    import subprocess
    import shutil
    from pathlib import Path
    from core.i18n import t
    from software.base import InstallError

    Path(cert_dir).mkdir(parents=True, exist_ok=True)

    acme = shutil.which("acme.sh") or str(Path.home() / ".acme.sh" / "acme.sh")

    if not Path(acme).exists():
        _ACME_MIRRORS = ACME_INSTALL_MIRRORS
        tmp_script = Path("/tmp/acme-install.sh")
        installed = False
        for url in _ACME_MIRRORS:
            r = subprocess.run(
                ["curl", "-fsSL", "--max-time", "20", "-o", str(tmp_script), url],
                capture_output=True, text=True, timeout=25,
            )
            if r.returncode != 0 or not tmp_script.exists() or tmp_script.stat().st_size < 1024:
                continue
            r2 = subprocess.run(
                ["sh", str(tmp_script), "--install-online", "-m", email],
                capture_output=True, text=True, timeout=120,
            )
            acme = str(Path.home() / ".acme.sh" / "acme.sh")
            if Path(acme).exists():
                installed = True
                break
        if not installed:
            raise InstallError(t("wireguard.error.acme_install_fail"))

    subprocess.run(
        [acme, "--register-account", "-m", email],
        check=False, capture_output=True, text=True, timeout=30,
    )

    subprocess.run(
        ["systemctl", "reload", "nginx"],
        check=False, capture_output=True, text=True,
    )

    cert_file = Path(f"{cert_dir}/{sni}.cer")
    if cert_file.exists():
        try:
            import datetime
            r_check = subprocess.run(
                ["openssl", "x509", "-noout", "-enddate", "-in", str(cert_file)],
                capture_output=True, text=True, timeout=10,
            )
            if r_check.returncode == 0:
                end_str = r_check.stdout.strip().replace("notAfter=", "")
                end_dt = datetime.datetime.strptime(end_str, "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=datetime.timezone.utc
                )
                remaining = (end_dt - datetime.datetime.now(datetime.timezone.utc)).days
                if remaining > 30:
                    return
        except Exception:
            pass

    from core.paths import nginx_webroot
    r = subprocess.run(
        [acme, "--issue", "-d", sni, "--webroot", str(nginx_webroot())],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode not in (0, 2):
        detail = r.stderr[-400:] if r.stderr else r.stdout[-400:]
        raise InstallError(t("wireguard.error.cert_issue_fail", detail=detail))

    subprocess.run(
        [acme, "--install-cert", "-d", sni,
         "--fullchain-file", f"{cert_dir}/{sni}.cer",
         "--key-file",       f"{cert_dir}/{sni}.key",
         "--reloadcmd",      "systemctl reload nginx"],
        check=True, capture_output=True, text=True, timeout=30,
    )


def _install_wireguard_pkg(os_id: str) -> None:
    """根据发行版安装 WireGuard 包"""
    from core.i18n import t
    from software.base import InstallError
    from core.pkg_runner import get_runner

    runner = get_runner()
    runner.update_index()

    if os_id == "centos" and _get_os_version() == 7:
        runner.install_extras(["epel-release", "elrepo-release"])
        runner.install(["kmod-wireguard", "wireguard-tools"])
    elif os_id in ("centos", "rocky", "almalinux", "rhel"):
        runner.install_extras(["epel-release"])
        runner.install(["wireguard-tools"])
    elif os_id in ("debian", "ubuntu", "fedora", "alpine"):
        runner.install(["wireguard", "wireguard-tools"])
    else:
        raise InstallError(t("wireguard.error.unsupported_wg", os_id=os_id))


def _ensure_xray_service(xray_path, service_name: str) -> None:
    """确保 xray systemd service 文件存在（带 CAP_NET_ADMIN）并 daemon-reload"""
    import subprocess
    from pathlib import Path
    from core.paths import xray_config_file
    service_path = Path(f"/etc/systemd/system/{service_name}.service")
    if not service_path.exists():
        service_path.write_text(
            "[Unit]\n"
            "Description=Xray Service\n"
            f"Documentation={XRAY_DOC_URL}\n"
            "After=network.target nss-lookup.target\n\n"
            "[Service]\n"
            "User=nobody\n"
            "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE\n"
            "AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE\n"
            "NoNewPrivileges=true\n"
            f"ExecStart={xray_path} run -config {xray_config_file()}\n"
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


def _install_xray() -> None:
    """安装 xray-core（多镜像回退，不依赖官方脚本）"""
    import subprocess
    import tempfile
    import zipfile
    import shutil
    from pathlib import Path
    from core.i18n import t
    from software.base import InstallError
    from wireguard.constants import XRAY_BINARY, XRAY_SERVICE

    from core.paths import xray_config_dir, xray_data_dir, xray_log_dir
    xray_path = Path(XRAY_BINARY)
    log_dir = xray_log_dir()

    if xray_path.exists():
        log_dir.mkdir(parents=True, exist_ok=True)
        xray_config_dir().mkdir(parents=True, exist_ok=True)
        xray_data_dir().mkdir(parents=True, exist_ok=True)
        _ensure_xray_service(xray_path, XRAY_SERVICE)
        return

    # ── 1. 获取最新版本号 ────────────────────────────────────────────────────
    import json
    import urllib.request
    ver = None
    api_urls = [XRAY_API_LATEST, XRAY_API_LATEST_GHPROXY]
    for api_url in api_urls:
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "opskit/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                ver = data.get("tag_name", "").lstrip("v")
                if ver:
                    break
        except Exception:
            continue

    if not ver:
        ver = "25.3.6"  # 已知可用的稳定版本兜底

    # ── 2. 下载 xray-core ────────────────────────────────────────────────────
    zip_name = XRAY_DOWNLOAD_ZIP
    github_path = f"{XRAY_REPO}/releases/download/v{ver}/{zip_name}"
    github_direct = f"{GITHUB_BASE}/{github_path}"
    url_template = f"{{mirror}}/https://github.com/{github_path}"

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / zip_name
        downloaded = False

        # ── 优先使用智能源管理下载（自动测速选源 + 断点续传）──
        try:
            from core.mirror import download as mirror_download
            mirror_download(url_template, zip_path, category="github_releases")
            if zip_path.exists() and zip_path.stat().st_size > 1_000_000:
                downloaded = True
            else:
                zip_path.unlink(missing_ok=True)
        except Exception:
            zip_path.unlink(missing_ok=True)

        # ── GitHub 直连兜底 ──
        if not downloaded:
            try:
                result = subprocess.run(
                    ["curl", "-fL",
                     "--connect-timeout", "10",
                     "--speed-time", "30", "--speed-limit", "10000",
                     "--max-time", "300",
                     "-s", "-o", str(zip_path), github_direct],
                    check=False, capture_output=True, text=True,
                )
                if result.returncode == 0 and zip_path.exists() and zip_path.stat().st_size > 1_000_000:
                    downloaded = True
                else:
                    zip_path.unlink(missing_ok=True)
            except Exception:
                zip_path.unlink(missing_ok=True)

        if not downloaded:
            raise InstallError(t("wireguard.error.xray_download_fail", ver=ver, url=github_direct, path=cache_zip))

        # ── 4. 解压并安装 ────────────────────────────────────────────────────
        extract_dir = Path(tmpdir) / "xray"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        src = extract_dir / "xray"
        if not src.exists():
            raise InstallError(t("wireguard.error.xray_zip_missing"))

        xray_path.parent.mkdir(parents=True, exist_ok=True)
        xray_data_dir().mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(xray_path))
        xray_path.chmod(0o755)

        for geo in ("geoip.dat", "geosite.dat"):
            geo_src = extract_dir / geo
            if geo_src.exists():
                shutil.copy2(str(geo_src), str(xray_data_dir() / geo))

    # ── 5. 创建 systemd service + 目录 ──────────────────────────────────────
    _ensure_xray_service(xray_path, XRAY_SERVICE)
    log_dir.mkdir(parents=True, exist_ok=True)
    xray_config_dir().mkdir(parents=True, exist_ok=True)
    xray_data_dir().mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    try:
        import pwd as _pwd
        _nobody = _pwd.getpwnam("nobody")
        _shutil.chown(str(log_dir), user=_nobody.pw_uid, group=_nobody.pw_gid)
    except Exception:
        pass
