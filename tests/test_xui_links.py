from __future__ import annotations

from xui.links import build_vless_link


def test_build_vless_reality_tcp_link() -> None:
    link = build_vless_link(
        uuid="11111111-1111-1111-1111-111111111111",
        host="example.com",
        port=443,
        public_key="pubkey",
        sni="www.cloudflare.com",
        short_id="abcdef1234567890",
        remark="opskit node",
    )
    assert link.startswith("vless://11111111-1111-1111-1111-111111111111@example.com:443?")
    assert "type=tcp" in link
    assert "security=reality" in link
    assert "pbk=pubkey" in link
    assert "flow=xtls-rprx-vision" in link
    assert "xhttp" not in link
    assert "path=" not in link
    assert "mode=" not in link
    assert link.endswith("#opskit%20node")
