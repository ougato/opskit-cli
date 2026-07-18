"""WireGuard + xray + nginx 配置模板"""
from __future__ import annotations


def xray_client_config(
    sni: str,
    server_port: int,
    uuid: str,
    local_port: int,
    wg_port: int,
    ws_path: str = "/vless-ws",
    fake_sni: str | None = None,
) -> str:
    """生成 xray 客户端 config.json（VLESS + WebSocket + TLS 模式）"""
    import json
    import random
    from core.paths import xray_log_dir
    from wireguard.constants import SNI_WHITELIST
    _log = xray_log_dir()
    _fake_sni = fake_sni or random.choice(SNI_WHITELIST)
    config = {
        "log": {
            "loglevel": "warning",
            "access": str(_log / "access.log"),
            "error": str(_log / "error.log"),
        },
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": local_port,
                "protocol": "dokodemo-door",
                "settings": {
                    "address": "127.0.0.1",
                    "port": wg_port,
                    "network": "udp",
                    "followRedirect": False,
                },
                "tag": "wg-udp-in",
            }
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": sni,
                            "port": server_port,
                            "users": [
                                {
                                    "id": uuid,
                                    "encryption": "none",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": {
                        "serverName": _fake_sni,
                        "allowInsecure": True,
                        "fingerprint": "chrome",
                    },
                    "wsSettings": {
                        "path": ws_path,
                        "host": sni,
                    },
                },
                "tag": "proxy",
            },
            {
                "protocol": "freedom",
                "tag": "direct",
            },
        ],
    }
    return json.dumps(config, indent=2, ensure_ascii=False)


def wg_watchdog_script(
    stale_secs: int = 180,
    escalate_fails: int = 3,
) -> str:
    """生成隧道看门狗脚本（按 label 检测握手新鲜度，过期重启 xray，连续失败升级重启 WG）"""
    return f"""#!/bin/sh
# OpsKit WireGuard 隧道看门狗
# 用法: opskit-wg-watchdog.sh <label>
label="$1"
[ -n "$label" ] || exit 1
iface="wg-$label"
stale={stale_secs}
state="/run/opskit-wg-watchdog-$label.fails"

hs=$(wg show "$iface" latest-handshakes 2>/dev/null | awk 'NR==1{{print $2}}')
if [ -z "$hs" ]; then
    logger -t opskit-wg-watchdog "$iface missing, restarting wg-quick@$iface"
    systemctl restart "xray@$label"
    systemctl restart "wg-quick@$iface"
    exit 0
fi
now=$(date +%s)
if [ "$hs" -gt 0 ] && [ $((now - hs)) -le "$stale" ]; then
    rm -f "$state"
    exit 0
fi
fails=$(cat "$state" 2>/dev/null || echo 0)
fails=$((fails + 1))
echo "$fails" > "$state"
logger -t opskit-wg-watchdog "$iface handshake stale (fails=$fails), restarting xray@$label"
systemctl restart "xray@$label"
if [ "$fails" -ge {escalate_fails} ]; then
    logger -t opskit-wg-watchdog "$iface still stale, restarting wg-quick@$iface"
    systemctl restart "wg-quick@$iface"
    echo 0 > "$state"
fi
"""


def wg_watchdog_service_unit(script_path: str) -> str:
    """生成看门狗 systemd 模板 service 单元（%i 为隧道 label）"""
    return f"""[Unit]
Description=OpsKit WireGuard tunnel watchdog - %i

[Service]
Type=oneshot
ExecStart={script_path} %i
"""


def wg_watchdog_timer_unit(interval: int = 30) -> str:
    """生成看门狗 systemd 模板 timer 单元（%i 为隧道 label）"""
    return f"""[Unit]
Description=OpsKit WireGuard tunnel watchdog timer - %i

[Timer]
OnBootSec={interval}
OnUnitActiveSec={interval}
AccuracySec=5

[Install]
WantedBy=timers.target
"""


def wg_server_config(
    server_private_key: str,
    server_ip: str,
    wg_port: int,
    iface: str,
    vpn_subnet: str = "10.10.10.0/24",
) -> str:
    """生成 WireGuard 服务端 wg0.conf（不含 peer，peer 由 wg set 动态添加）"""
    return f"""[Interface]
PrivateKey = {server_private_key}
Address = {server_ip}/24
ListenPort = {wg_port}
PostUp = iptables -I FORWARD 1 -i wg0 -j ACCEPT; iptables -I FORWARD 1 -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -s {vpn_subnet} -o {iface} -j MASQUERADE; iptables -t raw -I PREROUTING 1 ! -i wg0 -d {vpn_subnet} -j DROP
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -s {vpn_subnet} -o {iface} -j MASQUERADE; iptables -t raw -D PREROUTING ! -i wg0 -d {vpn_subnet} -j DROP
"""


def wg_peer_section(
    client_public_key: str,
    client_ip: str,
    psk: str,
    keepalive: int = 25,
) -> str:
    """生成 WireGuard 服务端 peer 段落（追加到 wg0.conf）"""
    return f"""
[Peer]
PublicKey = {client_public_key}
PresharedKey = {psk}
AllowedIPs = {client_ip}/32
PersistentKeepalive = {keepalive}
"""


def wg_client_config(
    client_private_key: str,
    client_ip: str,
    server_public_key: str,
    psk: str,
    server_endpoint: str,
    mtu: int = 1280,
    keepalive: int = 25,
    vpn_subnet: str = "10.10.10.0/24",
    dns: str | None = None,
) -> str:
    """生成 WireGuard 客户端 wg0.conf"""
    _dns_line = f"\nDNS = {dns}" if dns else ""
    return f"""[Interface]
PrivateKey = {client_private_key}
Address = {client_ip}/24
MTU = {mtu}{_dns_line}
PostUp = nmcli device set %i managed no 2>/dev/null || true

[Peer]
PublicKey = {server_public_key}
PresharedKey = {psk}
AllowedIPs = {vpn_subnet}
PersistentKeepalive = {keepalive}
Endpoint = {server_endpoint}
"""


def xray_server_config_ws(
    uuid: str,
    ws_port: int,
    ws_path: str = "/vless-ws",
) -> str:
    """生成 xray 服务端 config.json（VLESS + WebSocket 模式，供 nginx 反代）"""
    import json
    from core.paths import xray_log_dir
    _log = xray_log_dir()
    config = {
        "log": {
            "loglevel": "warning",
            "access": str(_log / "access.log"),
            "error": str(_log / "error.log"),
        },
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": ws_port,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": uuid}],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "none",
                    "wsSettings": {
                        "path": ws_path,
                    },
                },
                "sniffing": {"enabled": False},
            }
        ],
        "outbounds": [
            {
                "protocol": "freedom",
                "tag": "direct",
            },
        ],
    }
    return json.dumps(config, indent=2, ensure_ascii=False)


def nginx_http_only_config(sni: str) -> str:
    """生成 nginx 仅监听 80 端口的临时配置（HTTP-01 证书验证用）"""
    from core.paths import nginx_webroot
    _webroot = nginx_webroot()
    return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {sni};

    location /.well-known/acme-challenge/ {{
        root {_webroot};
    }}

    location / {{
        return 200 "OK\\n";
        add_header Content-Type text/plain;
    }}
}}
"""


def nginx_vless_ws_config(
    sni: str,
    ws_port: int,
    cert_dir: str | None = None,
    ws_path: str = "/vless-ws",
) -> str:
    """生成 nginx VLESS+WS+TLS 配置（反代 xray ws 服务端，含 80→443 重定向）"""
    if cert_dir is None:
        from core.paths import nginx_ssl_dir
        cert_dir = str(nginx_ssl_dir())
    return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {sni};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {sni};

    ssl_certificate     {cert_dir}/{sni}.cer;
    ssl_certificate_key {cert_dir}/{sni}.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location {ws_path} {{
        proxy_pass          http://127.0.0.1:{ws_port};
        proxy_http_version  1.1;
        proxy_set_header    Upgrade $http_upgrade;
        proxy_set_header    Connection "upgrade";
        proxy_set_header    Host $host;
        proxy_set_header    X-Real-IP $remote_addr;
        proxy_read_timeout  86400s;
        proxy_send_timeout  86400s;
    }}

    location / {{
        return 200 "OK\\n";
        add_header Content-Type text/plain;
    }}
}}
"""

