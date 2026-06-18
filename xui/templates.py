"""x-ui / Xray 入站模板。"""
from __future__ import annotations

import json

from xui.constants import (
    DEFAULT_TROJAN_REMARK,
    DEFAULT_VLESS_REMARK,
    DEFAULT_XHTTP_MODE,
    REALITY_SECURITY,
    SNIFFING_DEST_OVERRIDE,
    TCP_NETWORK,
    TLS_SECURITY,
    TROJAN_SNIFFING_DEST_OVERRIDE,
    TROJAN_PROTOCOL,
    VLESS_PROTOCOL,
    VLESS_DECRYPTION,
    XHTTP_NETWORK,
)


def vless_reality_xhttp_inbound(
    *,
    port: int,
    uuid: str,
    private_key: str,
    short_id: str,
    sni: str,
    dest: str,
    path: str,
    remark: str = DEFAULT_VLESS_REMARK,
    mode: str = DEFAULT_XHTTP_MODE,
) -> dict[str, object]:
    return {
        "remark": remark,
        "protocol": VLESS_PROTOCOL,
        "port": port,
        "settings": {
            "clients": [{"id": uuid, "email": remark}],
            "decryption": VLESS_DECRYPTION,
        },
        "streamSettings": {
            "network": XHTTP_NETWORK,
            "security": REALITY_SECURITY,
            "realitySettings": {
                "show": False,
                "dest": dest,
                "xver": 0,
                "serverNames": [sni],
                "privateKey": private_key,
                "shortIds": [short_id],
            },
            "xhttpSettings": {
                "path": path,
                "mode": mode,
            },
        },
        "sniffing": {
            "enabled": True,
            "destOverride": SNIFFING_DEST_OVERRIDE,
        },
    }


def trojan_inbound(
    *,
    port: int,
    password: str,
    sni: str,
    remark: str = DEFAULT_TROJAN_REMARK,
) -> dict[str, object]:
    return {
        "remark": remark,
        "protocol": TROJAN_PROTOCOL,
        "port": port,
        "settings": {
            "clients": [{"password": password, "email": remark}],
        },
        "streamSettings": {
            "network": TCP_NETWORK,
            "security": TLS_SECURITY,
            "tlsSettings": {"serverName": sni},
        },
        "sniffing": {
            "enabled": True,
            "destOverride": TROJAN_SNIFFING_DEST_OVERRIDE,
        },
    }


def to_xui_api_payload(inbound: dict[str, object]) -> dict[str, object]:
    settings = inbound.get("settings")
    stream_settings = inbound.get("streamSettings")
    sniffing = inbound.get("sniffing")
    return {
        "up": 0,
        "down": 0,
        "total": 0,
        "remark": inbound["remark"],
        "enable": True,
        "expiryTime": 0,
        "listen": "",
        "port": inbound["port"],
        "protocol": inbound["protocol"],
        "settings": json.dumps(settings, ensure_ascii=False),
        "streamSettings": json.dumps(stream_settings, ensure_ascii=False),
        "sniffing": json.dumps(sniffing, ensure_ascii=False),
    }
