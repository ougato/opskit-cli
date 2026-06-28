"""x-ui / Xray 入站模板。"""
from __future__ import annotations

import json

from xui.constants import (
    CLIENT_TOTAL_GB,
    DEFAULT_VLESS_REMARK,
    REALITY_SECURITY,
    SNIFFING_DEST_OVERRIDE,
    TCP_NETWORK,
    VLESS_FLOW,
    VLESS_PROTOCOL,
    VLESS_DECRYPTION,
)


def _client_defaults(email: str) -> dict[str, object]:
    return {
        "email": email,
        "enable": True,
        "limitIp": 0,
        "totalGB": CLIENT_TOTAL_GB,
        "expiryTime": 0,
        "tgId": 0,
        "subId": "",
        "comment": "",
        "reset": 0,
    }


def vless_reality_tcp_inbound(
    *,
    port: int,
    uuid: str,
    private_key: str,
    short_id: str,
    sni: str,
    dest: str,
    remark: str = DEFAULT_VLESS_REMARK,
) -> dict[str, object]:
    return {
        "remark": remark,
        "protocol": VLESS_PROTOCOL,
        "port": port,
        "settings": {
            "clients": [{"id": uuid, "flow": VLESS_FLOW, **_client_defaults(remark)}],
            "decryption": VLESS_DECRYPTION,
        },
        "streamSettings": {
            "network": TCP_NETWORK,
            "security": REALITY_SECURITY,
            "realitySettings": {
                "show": False,
                "dest": dest,
                "xver": 0,
                "serverNames": [sni],
                "privateKey": private_key,
                "shortIds": [short_id],
            },
        },
        "sniffing": {
            "enabled": True,
            "destOverride": SNIFFING_DEST_OVERRIDE,
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
