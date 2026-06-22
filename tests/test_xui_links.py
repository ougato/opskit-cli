from __future__ import annotations

from xui.links import build_trojan_link, build_vless_link


def test_build_vless_reality_xhttp_link() -> None:
    link = build_vless_link(
        uuid="11111111-1111-1111-1111-111111111111",
        host="example.com",
        port=443,
        public_key="pubkey",
        sni="www.cloudflare.com",
        short_id="abcdef1234567890",
        path="/xhttp-a1b2c3",
        remark="opskit node",
    )
    assert link.startswith("vless://11111111-1111-1111-1111-111111111111@example.com:443?")
    assert "type=xhttp" in link
    assert "security=reality" in link
    assert "pbk=pubkey" in link
    assert "path=/xhttp-a1b2c3" in link
    assert link.endswith("#opskit%20node")


def test_build_trojan_link() -> None:
    link = build_trojan_link(
        password="pass word",
        host="example.com",
        port=8443,
        sni="example.com",
        remark="trojan node",
        allow_insecure=True,
    )
    assert link.startswith("trojan://pass%20word@example.com:8443?")
    assert "security=tls" in link
    assert "sni=example.com" in link
    assert "allowInsecure=1" in link
    assert link.endswith("#trojan%20node")
