"""x-ui 分享链接生成。"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from xui.constants import (
    CLIENT_FINGERPRINT,
    DEFAULT_FINGERPRINT,
    DEFAULT_XHTTP_MODE,
    REALITY_SECURITY,
    TCP_NETWORK,
    TLS_SECURITY,
    TROJAN_ALLOW_INSECURE,
    TROJAN_PROTOCOL,
    XHTTP_NETWORK,
)


def build_vless_link(
    *,
    uuid: str,
    host: str,
    port: int,
    public_key: str,
    sni: str,
    short_id: str,
    path: str,
    remark: str,
    fingerprint: str = DEFAULT_FINGERPRINT,
    mode: str = DEFAULT_XHTTP_MODE,
) -> str:
    query = urlencode(
        {
            "type": XHTTP_NETWORK,
            "security": REALITY_SECURITY,
            "pbk": public_key,
            "fp": fingerprint,
            "sni": sni,
            "sid": short_id,
            "path": path,
            "mode": mode,
        },
        safe="/",
    )
    return f"vless://{quote(uuid, safe='')}@{host}:{port}?{query}#{quote(remark)}"


def build_trojan_link(
    *,
    password: str,
    host: str,
    port: int,
    sni: str,
    remark: str,
    allow_insecure: bool = False,
) -> str:
    query_params = {
        "security": TLS_SECURITY,
        "sni": sni,
        "type": TCP_NETWORK,
    }
    if allow_insecure:
        query_params["allowInsecure"] = TROJAN_ALLOW_INSECURE
    query = urlencode(
        query_params
    )
    return f"{TROJAN_PROTOCOL}://{quote(password, safe='')}@{host}:{port}?{query}#{quote(remark)}"
