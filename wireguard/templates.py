"""WireGuard + xray + nginx 配置模板"""
from __future__ import annotations


def xray_client_config(
    sni: str,
    server_port: int,
    uuid: str,
    local_port: int,
    wg_port: int,
    ws_path: str = "/vless-ws",
) -> str:
    """生成 xray 客户端 config.json（VLESS + WebSocket + TLS 模式）"""
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
                        "serverName": "www.microsoft.com",
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


def wg_server_config(
    server_private_key: str,
    server_ip: str,
    wg_port: int,
    iface: str,
) -> str:
    """生成 WireGuard 服务端 wg0.conf（不含 peer，peer 由 wg set 动态添加）"""
    return f"""[Interface]
PrivateKey = {server_private_key}
Address = {server_ip}/24
ListenPort = {wg_port}
PostUp = iptables -I FORWARD 1 -i wg0 -j ACCEPT; iptables -I FORWARD 1 -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o {iface} -j MASQUERADE; iptables -t raw -I PREROUTING 1 ! -i wg0 -d 10.10.10.0/24 -j DROP
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -s 10.10.10.0/24 -o {iface} -j MASQUERADE; iptables -t raw -D PREROUTING ! -i wg0 -d 10.10.10.0/24 -j DROP
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
) -> str:
    """生成 WireGuard 客户端 wg0.conf"""
    return f"""[Interface]
PrivateKey = {client_private_key}
Address = {client_ip}/24
MTU = {mtu}
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

