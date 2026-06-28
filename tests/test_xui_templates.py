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
    assert client["totalGB"] > 0
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
