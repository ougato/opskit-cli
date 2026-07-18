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
        fields = dict(self._FIELDS)
        tok = encode_token(fields)
        assert "v" not in fields
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

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("client_ip", "10.10.20.2\nPostUp = touch /tmp/pwned"),
            ("vpn_subnet", "10.10.20.0/24\nAllowedIPs = 0.0.0.0/0"),
            ("server_port", 70000),
            ("uuid", "not-a-uuid"),
            ("sni", "bad.example.com\nserver_name evil"),
            ("dns", "10.10.20.1\nPostUp = touch /tmp/pwned"),
        ],
    )
    def test_invalid_token_field_rejected(self, field, value):
        from wireguard.token import encode_token, decode_token

        fields = dict(self._FIELDS)
        fields[field] = value
        tok = encode_token(fields)

        with pytest.raises(ValueError):
            decode_token(tok)


# ─── 敏感文件写入 ─────────────────────────────────────────────────────────────

class TestWireGuardSecretFiles:
    """WireGuard state/config 类敏感文件必须限制权限"""

    def test_write_secret_file_sets_0600(self, tmp_path):
        from wireguard.utils import write_secret_file

        target = tmp_path / "wg0.conf"
        write_secret_file(str(target), "PrivateKey = secret\n")

        assert target.read_text(encoding="utf-8") == "PrivateKey = secret\n"
        if os.name != "nt":
            assert (target.stat().st_mode & 0o777) == 0o600


class TestTunnelLabel:
    """隧道 label 规范化应在服务端/客户端复用同一规则"""

    def test_normalize_tunnel_label_replaces_unsafe_chars(self):
        from wireguard.utils import normalize_tunnel_label

        assert normalize_tunnel_label(" hk cn/01 ", default="default") == "hk-cn-01"

    def test_normalize_tunnel_label_limits_length_and_fallback(self):
        from wireguard.utils import normalize_tunnel_label

        assert normalize_tunnel_label("a" * 30, default="default") == "a" * 24
        assert normalize_tunnel_label("///", default="server") == "server"


# ─── 配方文案与卸载安全边界 ───────────────────────────────────────────────────

class TestWireGuardRecipeSafety:
    """锁住 WG 当前协议描述和卸载时的共享资源保护边界"""

    def test_recipe_description_matches_ws_tls_transport(self):
        from software.recipes.wireguard.recipe import WgClientRecipe, WgServerRecipe

        assert "VLESS WS TLS" in WgServerRecipe.description
        assert "VLESS WS TLS" in WgClientRecipe.description
        assert "REALITY" not in WgServerRecipe.description
        assert "REALITY" not in WgClientRecipe.description

    def test_uninstall_preserves_shared_xray_runtime(self):
        import inspect
        from wireguard import client, server

        combined = inspect.getsource(server.uninstall_server) + inspect.getsource(client.uninstall_client)

        assert "XRAY_BINARY" not in combined
        assert "/etc/systemd/system/xray@.service" not in combined
        assert "/etc/systemd/system/xray.service" not in combined
        assert "xray_log_dir" not in combined
        assert "xray_data_dir" not in combined

    def test_client_uninstall_does_not_remove_server_sysctl(self):
        import inspect
        from wireguard import client

        source = inspect.getsource(client.uninstall_client)

        assert "/etc/sysctl.d/99-wg.conf" not in source

    def test_xray_log_permissions_use_runtime_nobody_group(self):
        import inspect
        from wireguard import utils

        source = inspect.getsource(utils.install_xray)
        helper_source = inspect.getsource(utils.ensure_xray_runtime_permissions)

        assert "nobody:nogroup" not in source
        assert "id -gn nobody" in helper_source
        assert "chown -R" in helper_source

    def test_server_uninstall_removes_server_state(self):
        import inspect
        from wireguard import server

        source = inspect.getsource(server.uninstall_server)

        assert "WG_STATE_FILE" in source
        assert 'run_as_root(["rm", "-f", WG_STATE_FILE' in source

    def test_install_marks_installed_after_service_verification(self):
        import inspect
        from wireguard import client, server

        server_source = inspect.getsource(server.install_server)
        assert server_source.index("service_start_fail") < server_source.index('mark_installed("wg_server")')

        client_source = inspect.getsource(client._install_client_token)
        assert client_source.index("client_service_start_fail") < client_source.index("mark_installed")

    def test_server_state_saved_after_service_verification(self):
        import inspect
        from wireguard import server

        source = inspect.getsource(server.install_server)

        assert source.index("service_start_fail") < source.index("_save_state(state)")
        assert source.index("_save_state(state)") < source.index('mark_installed("wg_server")')

    def test_client_failed_install_cleans_orphan_runtime(self):
        import inspect
        from wireguard import client

        source = inspect.getsource(client._install_client_token)
        failure_block = source[source.index("client_service_start_fail"):source.index("pause()", source.index("client_service_start_fail"))]

        assert "stop_and_disable(wg_svc)" in failure_block
        assert "stop_and_disable(xray_svc)" in failure_block
        assert 'run_as_root(["rm", "-f", wg_cfg_path, xray_cfg_path' in failure_block
        assert "_SCM.remove" in failure_block

    def test_server_manage_menu_has_no_manual_dns_entry(self):
        import inspect
        from wireguard import server

        source = inspect.getsource(server.manage_peers)

        assert "setup_dns" not in source
        assert "_run_setup_dns" not in source


# ─── _alloc_local_port ────────────────────────────────────────────────────────

class TestPublicIpDetection:
    def test_uses_parallel_valid_ip_sources(self):
        import inspect
        from core.constants import PUBLIC_IP_APIS
        from wireguard import server

        source = inspect.getsource(server._detect_public_ip)

        assert "https://ip.sb" in PUBLIC_IP_APIS
        assert "ThreadPoolExecutor" in source
        assert "ipaddress.ip_address" in source
        assert '"." in ip' not in source


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

    def test_full_port_range_returns_none(self):
        from wireguard.client import _alloc_local_port
        from wireguard.constants import CLIENT_XRAY_LOCAL_PORT_MIN, CLIENT_XRAY_LOCAL_PORT_MAX
        tunnels = [
            {"local_port": p}
            for p in range(CLIENT_XRAY_LOCAL_PORT_MIN, CLIENT_XRAY_LOCAL_PORT_MAX + 1)
        ]
        assert _alloc_local_port(tunnels) is None


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


class TestXrayServerDiagnose:
    """服务端诊断应兼容当前 WS/TLS xray 配置"""

    def test_ws_tls_config_extracts_uuid_and_state_compat_fields(self):
        from wireguard.server import _extract_xray_client_creds
        from wireguard.templates import xray_server_config_ws

        cfg = json.loads(xray_server_config_ws(
            uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            ws_port=2443,
            ws_path="/vless-ws",
        ))
        creds = _extract_xray_client_creds(
            cfg,
            {"xray_pub": "PUB_FROM_STATE", "short_id": "SHORT_FROM_STATE"},
        )

        assert creds == (
            "PUB_FROM_STATE",
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "SHORT_FROM_STATE",
            None,
        )


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
