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


def test_ipv6_link_and_explicit_encryption():
    from urllib.parse import urlsplit, parse_qs
    link = build_vless_link(uuid="synthetic", host="2001:db8::1", port=443, public_key="synthetic",
                            sni="example.invalid", short_id="aabb", remark="test")
    parsed = urlsplit(link)
    assert parsed.hostname == "2001:db8::1"
    assert parsed.port == 443
    assert parse_qs(parsed.query)["encryption"] == ["none"]


def test_manage_link_writes_complete_uri_at_narrow_console_width(monkeypatch):
    from io import StringIO

    from rich.console import Console

    from xui import server

    output = StringIO()
    monkeypatch.setattr(server, "console", Console(file=output, width=24, force_terminal=True))
    link = "vless://test@host:443?security=reality&pbk=" + "a" * 43 + "&sid=abcd#test"
    server._print_links({"vless": {"link": link}})

    assert output.getvalue().endswith("\n" + link + "\n")
