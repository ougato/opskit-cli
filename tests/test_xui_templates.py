from __future__ import annotations

import json

from xui.templates import to_xui_api_payload, trojan_inbound, vless_reality_xhttp_inbound


def test_vless_reality_xhttp_template() -> None:
    inbound = vless_reality_xhttp_inbound(
        port=443,
        uuid="uuid",
        private_key="priv",
        short_id="sid",
        sni="www.cloudflare.com",
        dest="www.cloudflare.com:443",
        path="/xhttp-test",
    )
    assert inbound["protocol"] == "vless"
    assert inbound["port"] == 443
    stream = inbound["streamSettings"]
    assert isinstance(stream, dict)
    assert stream["network"] == "xhttp"
    assert stream["security"] == "reality"
    settings = stream["realitySettings"]
    assert isinstance(settings, dict)
    assert settings["privateKey"] == "priv"
    assert settings["shortIds"] == ["sid"]


def test_trojan_template_and_api_payload() -> None:
    inbound = trojan_inbound(
        port=8443,
        password="secret",
        sni="example.com",
    )
    assert inbound["protocol"] == "trojan"
    payload = to_xui_api_payload(inbound)
    assert payload["protocol"] == "trojan"
    assert payload["port"] == 8443
    settings = json.loads(str(payload["settings"]))
    assert settings["clients"][0]["password"] == "secret"
