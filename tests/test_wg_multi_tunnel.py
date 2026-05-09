"""WireGuard 多隧道共存单元测试"""
from __future__ import annotations

import json
import sys
import os
import pytest

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── token v2 编解码 ──────────────────────────────────────────────────────────

class TestTokenV2:
    """令牌 v2 格式编解码测试"""

    _FIELDS = {
        "server_ip":       "1.2.3.4",
        "server_port":     443,
        "wg_server_pubkey": "FAKE_WG_PUB==",
        "wg_client_privkey": "FAKE_WG_PRIV==",
        "wg_psk":          "FAKE_PSK==",
        "client_ip":       "10.10.20.2",
        "reality_pubkey":  "FAKE_REALITY_PUB==",
        "uuid":            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "short_id":        "abcd1234",
        "sni":             "example.com",
        "vpn_subnet":      "10.10.20.0/24",
        "vpn_gateway":     "10.10.20.1",
        "label":           "hk",
    }

    def test_encode_decode_v2(self):
        from wireguard.token import encode_token, decode_token
        tok = encode_token(self._FIELDS)
        assert isinstance(tok, str)
        result = decode_token(tok)
        for k, v in self._FIELDS.items():
            assert result[k] == v, f"field {k} mismatch: {result[k]!r} != {v!r}"

    def test_version_field(self):
        from wireguard.token import encode_token, TOKEN_PREFIX
        import base64
        import gzip
        tok = encode_token(dict(self._FIELDS))
        b64_part = tok[len(TOKEN_PREFIX):]
        compressed = base64.urlsafe_b64decode(b64_part + "==")
        data = json.loads(gzip.decompress(compressed))
        assert data.get("v") == 2

    def test_v1_compat(self):
        """v1 令牌（无 vpn_subnet/vpn_gateway/label）解码后应补默认值"""
        from wireguard.token import decode_token, TOKEN_PREFIX
        import base64
        import gzip
        v1_fields = {k: v for k, v in self._FIELDS.items()
                     if k not in ("vpn_subnet", "vpn_gateway", "label")}
        # 手动构造 gzip 压缩的 v1 令牌（无 vpn_subnet/vpn_gateway/label 字段）
        raw = gzip.compress(json.dumps(v1_fields, separators=(",", ":")).encode("utf-8"))
        tok = TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")
        result = decode_token(tok)
        assert result["vpn_subnet"]  == "10.10.10.0/24"
        assert result["vpn_gateway"] == "10.10.10.1"
        assert result["label"]       == "default"

    def test_missing_required_field_raises(self):
        from wireguard.token import encode_token, decode_token
        bad = dict(self._FIELDS)
        del bad["server_ip"]
        tok = encode_token(bad)
        with pytest.raises(ValueError):
            decode_token(tok)

    def test_label_preserved(self):
        from wireguard.token import encode_token, decode_token
        fields = dict(self._FIELDS)
        fields["label"] = "aliyun"
        tok = encode_token(fields)
        result = decode_token(tok)
        assert result["label"] == "aliyun"


# ─── _alloc_local_port ────────────────────────────────────────────────────────

class TestAllocLocalPort:
    """客户端本地端口自动分配测试"""

    def test_empty_tunnels(self):
        from wireguard.client import _alloc_local_port
        from wireguard.constants import CLIENT_XRAY_LOCAL_PORT_MIN
        assert _alloc_local_port([]) == CLIENT_XRAY_LOCAL_PORT_MIN

    def test_skip_used_ports(self):
        from wireguard.client import _alloc_local_port
        from wireguard.constants import CLIENT_XRAY_LOCAL_PORT_MIN
        tunnels = [{"local_port": CLIENT_XRAY_LOCAL_PORT_MIN}]
        assert _alloc_local_port(tunnels) == CLIENT_XRAY_LOCAL_PORT_MIN + 1

    def test_multiple_gaps(self):
        from wireguard.client import _alloc_local_port
        from wireguard.constants import CLIENT_XRAY_LOCAL_PORT_MIN
        used = [CLIENT_XRAY_LOCAL_PORT_MIN, CLIENT_XRAY_LOCAL_PORT_MIN + 1, CLIENT_XRAY_LOCAL_PORT_MIN + 3]
        tunnels = [{"local_port": p} for p in used]
        assert _alloc_local_port(tunnels) == CLIENT_XRAY_LOCAL_PORT_MIN + 2

    def test_no_local_port_field(self):
        from wireguard.client import _alloc_local_port
        from wireguard.constants import CLIENT_XRAY_LOCAL_PORT_MIN
        tunnels = [{"label": "x"}, {"label": "y"}]
        assert _alloc_local_port(tunnels) == CLIENT_XRAY_LOCAL_PORT_MIN


# ─── _alloc_client_ip (server) ───────────────────────────────────────────────

class TestAllocClientIp:
    """服务端客户端 IP 分配测试"""

    def test_empty_clients(self):
        from wireguard.server import _alloc_client_ip
        from wireguard.constants import VPN_CLIENT_IP_START
        assert _alloc_client_ip([]) == VPN_CLIENT_IP_START

    def test_skip_used(self):
        from wireguard.server import _alloc_client_ip
        from wireguard.constants import VPN_CLIENT_IP_START
        clients = [{"ip": f"10.10.10.{VPN_CLIENT_IP_START}"}]
        assert _alloc_client_ip(clients) == VPN_CLIENT_IP_START + 1

    def test_gaps_filled(self):
        from wireguard.server import _alloc_client_ip
        from wireguard.constants import VPN_CLIENT_IP_START
        used = [VPN_CLIENT_IP_START, VPN_CLIENT_IP_START + 1, VPN_CLIENT_IP_START + 3]
        clients = [{"ip": f"10.10.10.{s}"} for s in used]
        assert _alloc_client_ip(clients) == VPN_CLIENT_IP_START + 2

    def test_full_returns_none(self):
        from wireguard.server import _alloc_client_ip
        from wireguard.constants import VPN_CLIENT_IP_START, VPN_CLIENT_IP_MAX
        clients = [{"ip": f"10.10.10.{i}"} for i in range(VPN_CLIENT_IP_START, VPN_CLIENT_IP_MAX + 1)]
        assert _alloc_client_ip(clients) is None


# ─── _next_peer_ip (server) ──────────────────────────────────────────────────

class TestNextPeerIp:
    """服务端下一可用 peer IP 推算测试"""

    def test_empty(self):
        from wireguard.server import _next_peer_ip
        from wireguard.constants import VPN_PEER_IP_START
        result = _next_peer_ip([], octet3=10)
        assert result == f"10.10.10.{VPN_PEER_IP_START}/32"

    def test_custom_octet3(self):
        from wireguard.server import _next_peer_ip
        from wireguard.constants import VPN_PEER_IP_START
        result = _next_peer_ip([], octet3=20)
        assert result == f"10.10.20.{VPN_PEER_IP_START}/32"

    def test_skip_used(self):
        from wireguard.server import _next_peer_ip
        from wireguard.constants import VPN_PEER_IP_START
        peers = [{"allowed": f"10.10.10.{VPN_PEER_IP_START}/32"}]
        result = _next_peer_ip(peers, octet3=10)
        assert result == f"10.10.10.{VPN_PEER_IP_START + 1}/32"

    def test_octet3_isolation(self):
        """不同 octet3 的 peer 不相互干扰"""
        from wireguard.server import _next_peer_ip
        from wireguard.constants import VPN_PEER_IP_START
        peers = [{"allowed": f"10.10.20.{VPN_PEER_IP_START}/32"}]
        result = _next_peer_ip(peers, octet3=10)
        assert result == f"10.10.10.{VPN_PEER_IP_START}/32"


# ─── SNI 白名单 ───────────────────────────────────────────────────────────────

class TestSniWhitelist:
    """SNI 白名单完整性测试"""

    def test_not_empty(self):
        from wireguard.constants import SNI_WHITELIST
        assert len(SNI_WHITELIST) > 0

    def test_no_sensitive_domains(self):
        from wireguard.constants import SNI_WHITELIST
        sensitive = {"dl.google.com", "www.cloudflare.com"}
        assert not sensitive & set(SNI_WHITELIST), \
            f"敏感域名不应出现在白名单中: {sensitive & set(SNI_WHITELIST)}"

    def test_all_are_strings(self):
        from wireguard.constants import SNI_WHITELIST
        for d in SNI_WHITELIST:
            assert isinstance(d, str) and "." in d, f"非法域名: {d!r}"


# ─── wg_server_config 动态子网 ───────────────────────────────────────────────

class TestWgServerConfig:
    """wg_server_config 模板使用动态子网测试"""

    def test_default_subnet(self):
        from wireguard.templates import wg_server_config
        cfg = wg_server_config("PRIV==", "10.10.10.1", 3002, "eth0")
        assert "10.10.10.0/24" in cfg

    def test_custom_subnet(self):
        from wireguard.templates import wg_server_config
        cfg = wg_server_config("PRIV==", "10.10.20.1", 3002, "eth0", vpn_subnet="10.10.20.0/24")
        assert "10.10.20.0/24" in cfg
        assert "10.10.10.0/24" not in cfg


# ─── xray_client_config 随机 SNI ─────────────────────────────────────────────

class TestXrayClientConfig:
    """xray_client_config 随机 SNI 测试"""

    def test_fake_sni_from_whitelist(self):
        from wireguard.templates import xray_client_config
        from wireguard.constants import SNI_WHITELIST
        cfg_str = xray_client_config(
            sni="example.com", server_port=443, uuid="u-u-u-u",
            local_port=4000, wg_port=3002,
        )
        cfg = json.loads(cfg_str)
        tls = cfg["outbounds"][0]["streamSettings"]["tlsSettings"]
        assert tls["serverName"] in SNI_WHITELIST

    def test_explicit_fake_sni(self):
        from wireguard.templates import xray_client_config
        cfg_str = xray_client_config(
            sni="example.com", server_port=443, uuid="u-u-u-u",
            local_port=4000, wg_port=3002, fake_sni="www.microsoft.com",
        )
        cfg = json.loads(cfg_str)
        tls = cfg["outbounds"][0]["streamSettings"]["tlsSettings"]
        assert tls["serverName"] == "www.microsoft.com"

    def test_host_uses_sni(self):
        from wireguard.templates import xray_client_config
        cfg_str = xray_client_config(
            sni="my.domain.com", server_port=443, uuid="u-u-u-u",
            local_port=4000, wg_port=3002,
        )
        cfg = json.loads(cfg_str)
        ws = cfg["outbounds"][0]["streamSettings"]["wsSettings"]
        assert ws["host"] == "my.domain.com"
