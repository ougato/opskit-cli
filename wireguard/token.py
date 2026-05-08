"""连接令牌编码 / 解码 — 服务端生成、客户端消费"""
from __future__ import annotations

import base64
import gzip
import json

from core.i18n import t

TOKEN_PREFIX = "opskit://"
TOKEN_VERSION = 1


def encode_token(data: dict) -> str:
    """将连接参数字典编码为令牌字符串

    流程：dict → JSON bytes → gzip → base64 → 加前缀
    """
    data["v"] = TOKEN_VERSION
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9)
    b64 = base64.urlsafe_b64encode(compressed).decode("ascii")
    return f"{TOKEN_PREFIX}{b64}"


def decode_token(token: str) -> dict:
    """将令牌字符串解码为连接参数字典

    Raises:
        ValueError: 令牌格式无效
    """
    token = token.strip()
    if token.startswith(TOKEN_PREFIX):
        token = token[len(TOKEN_PREFIX):]

    try:
        compressed = base64.urlsafe_b64decode(token)
        raw = gzip.decompress(compressed)
        data = json.loads(raw)
    except Exception as e:
        raise ValueError(t("wireguard.error.token_decode_fail", error=e)) from e

    if not isinstance(data, dict):
        raise ValueError(t("wireguard.error.token_invalid_json"))

    required_keys = (
        "server_ip", "server_port", "wg_server_pubkey",
        "wg_client_privkey", "wg_psk", "client_ip",
        "uuid", "sni",
    )
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise ValueError(t("wireguard.error.token_missing_fields", fields=', '.join(missing)))

    return data
