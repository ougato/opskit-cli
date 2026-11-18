"""x-ui 分享链接生成。"""
from __future__ import annotations

from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from xui.constants import (
    DEFAULT_FINGERPRINT,
    REALITY_SECURITY,
    TCP_NETWORK,
    VLESS_FLOW,
    VLESS_DECRYPTION,
)


def build_vless_link(
    *,
    uuid: str,
    host: str,
    port: int,
    public_key: str,
    sni: str,
    short_id: str,
    remark: str,
    fingerprint: str = DEFAULT_FINGERPRINT,
) -> str:
    query = urlencode(
        {
            "encryption": VLESS_DECRYPTION,
            "type": TCP_NETWORK,
            "security": REALITY_SECURITY,
            "pbk": public_key,
            "fp": fingerprint,
            "sni": sni,
            "sid": short_id,
            "flow": VLESS_FLOW,
        },
        safe="/",
    )
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"vless://{quote(uuid, safe='')}@{host}:{port}?{query}#{quote(remark)}"

def mihomo_proxy_yaml(link: str) -> bytes:
    """Serialize a proxy list entry for use under Mihomo's proxies key."""
    import yaml

    try:
        uri = urlsplit(link)
        query = parse_qs(uri.query)
        if uri.scheme != "vless" or not uri.hostname or not uri.username or uri.port is None:
            raise ValueError
        if query["type"] != [TCP_NETWORK] or query["security"] != [REALITY_SECURITY]:
            raise ValueError
        proxy = {
            "name": unquote(uri.fragment) or "OpsKit VLESS",
            "type": "vless",
            "server": uri.hostname,
            "port": uri.port,
            "uuid": unquote(uri.username),
            "network": TCP_NETWORK,
            "udp": True,
            "tls": True,
            "servername": query["sni"][0],
            "client-fingerprint": query["fp"][0],
            "flow": query["flow"][0],
            "reality-opts": {
                "public-key": query["pbk"][0],
                "short-id": query.get("sid", [""])[0],
                "support-x25519mlkem768": True,
            },
        }
    except (KeyError, ValueError):
        raise ValueError("Invalid VLESS REALITY TCP link") from None
    return yaml.safe_dump([proxy], allow_unicode=True, sort_keys=False).encode("utf-8")
