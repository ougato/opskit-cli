from __future__ import annotations

import json

from xui.templates import to_xui_api_payload, vless_reality_tcp_inbound


def test_vless_reality_tcp_template() -> None:
    inbound = vless_reality_tcp_inbound(
        port=443,
        uuid="uuid",
        private_key="priv",
        short_id="sid",
        sni="www.cloudflare.com",
        dest="www.cloudflare.com:443",
    )
    assert inbound["protocol"] == "vless"
    assert inbound["port"] == 443
    client = inbound["settings"]["clients"][0]
    assert client["enable"] is True
    assert client["totalGB"] == 0
    assert client["expiryTime"] == 0
    assert client["flow"] == "xtls-rprx-vision"
    stream = inbound["streamSettings"]
    assert isinstance(stream, dict)
    assert stream["network"] == "tcp"
    assert stream["security"] == "reality"
    assert "xhttpSettings" not in stream
    settings = stream["realitySettings"]
    assert isinstance(settings, dict)
    assert settings["privateKey"] == "priv"
    assert settings["shortIds"] == ["sid"]


def test_vless_api_payload() -> None:
    inbound = vless_reality_tcp_inbound(
        port=443,
        uuid="uuid",
        private_key="priv",
        short_id="sid",
        sni="www.cloudflare.com",
        dest="www.cloudflare.com:443",
    )
    payload = to_xui_api_payload(inbound)
    assert payload["protocol"] == "vless"
    assert payload["port"] == 443
    stream = json.loads(str(payload["streamSettings"]))
    assert stream["network"] == "tcp"
    assert stream["security"] == "reality"

    client = json.loads(payload["settings"])["clients"][0]
    assert client["enable"] is True
    assert client["totalGB"] == client["expiryTime"] == 0
    assert payload["enable"] is True
    assert payload["total"] == payload["expiryTime"] == 0


def test_installer_sends_unlimited_client_through_panel_api(monkeypatch):
    from xui import server
    calls = []
    def add(*args):
        calls.append(args[-1])
        return True
    monkeypatch.setattr(server, "add_inbound", add)
    inbound = vless_reality_tcp_inbound(port=443, uuid="uuid", private_key="private", short_id="abcd",
                                       sni="example.invalid", dest="example.invalid:443")
    assert server._create_inbounds(panel_port=54321, panel_user="test", panel_password="synthetic",
                                  panel_base_path="/test/", inbounds=[inbound])
    client = json.loads(calls[0]["settings"])["clients"][0]
    assert client["totalGB"] == client["expiryTime"] == 0
    assert client["enable"] is True
