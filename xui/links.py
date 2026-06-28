"""x-ui 分享链接生成。"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from xui.constants import (
    DEFAULT_FINGERPRINT,
    REALITY_SECURITY,
    TCP_NETWORK,
    VLESS_FLOW,
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
    return f"vless://{quote(uuid, safe='')}@{host}:{port}?{query}#{quote(remark)}"
