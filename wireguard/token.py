"""连接令牌编码 / 解码 — 服务端生成、客户端消费"""
from __future__ import annotations

import base64
import gzip
import ipaddress
import json
import re
import uuid

from core.i18n import t

TOKEN_PREFIX = "opskit://"
TOKEN_VERSION = 2

TOKEN_V1_DEFAULTS = {
    "vpn_subnet": "10.10.10.0/24",
    "vpn_gateway": "10.10.10.1",
    "label": "default",
}

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$"
)
_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def _reject_control_chars(field: str, value: object) -> None:
    if not isinstance(value, str):
        raise ValueError(t("wireguard.error.token_invalid_field", field=field))
    if any(ord(ch) < 32 or ch == "\x7f" for ch in value):
        raise ValueError(t("wireguard.error.token_invalid_field", field=field))


def _validate_host(field: str, value: object) -> None:
    _reject_control_chars(field, value)
    text = str(value).strip()
    try:
        ipaddress.ip_address(text)
        return
    except ValueError:
        pass
    if not _DOMAIN_RE.match(text):
        raise ValueError(t("wireguard.error.token_invalid_field", field=field))


def _validate_ip(field: str, value: object) -> None:
    _reject_control_chars(field, value)
    try:
        ipaddress.ip_address(str(value).strip())
    except ValueError as e:
        raise ValueError(t("wireguard.error.token_invalid_field", field=field)) from e


def _validate_network(field: str, value: object) -> None:
    _reject_control_chars(field, value)
    try:
        ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError as e:
        raise ValueError(t("wireguard.error.token_invalid_field", field=field)) from e


def _validate_dns(field: str, value: object) -> None:
    _reject_control_chars(field, value)
    for part in str(value).split(","):
        item = part.strip()
        if not item:
            raise ValueError(t("wireguard.error.token_invalid_field", field=field))
        _validate_host(field, item)


def _validate_token_fields(data: dict) -> None:
    for field in (
        "wg_server_pubkey",
        "wg_client_privkey",
        "wg_psk",
        "reality_pubkey",
        "short_id",
    ):
        if field in data:
            _reject_control_chars(field, data[field])

    try:
        port = int(data["server_port"])
    except (TypeError, ValueError) as e:
        raise ValueError(t("wireguard.error.token_invalid_field", field="server_port")) from e
    if not 1 <= port <= 65535:
        raise ValueError(t("wireguard.error.token_invalid_field", field="server_port"))
    data["server_port"] = port

    _validate_host("server_ip", data["server_ip"])
    _validate_ip("client_ip", data["client_ip"])
    _validate_ip("vpn_gateway", data["vpn_gateway"])
    _validate_network("vpn_subnet", data["vpn_subnet"])
    _validate_host("sni", data["sni"])

    try:
        uuid.UUID(str(data["uuid"]))
    except (TypeError, ValueError) as e:
        raise ValueError(t("wireguard.error.token_invalid_field", field="uuid")) from e

    _reject_control_chars("label", data["label"])
    if not _LABEL_RE.match(str(data["label"])):
        raise ValueError(t("wireguard.error.token_invalid_field", field="label"))

    if data.get("dns"):
        _validate_dns("dns", data["dns"])


def encode_token(data: dict) -> str:
    """将连接参数字典编码为令牌字符串

    流程：dict → JSON bytes → gzip → base64 → 加前缀
    """
    payload = dict(data)
    payload["v"] = TOKEN_VERSION
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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

    for key, default in TOKEN_V1_DEFAULTS.items():
        if key not in data:
            data[key] = default

    _validate_token_fields(data)

    return data
