"""Repeated installs, recovery after lost replies and preservation of user data."""
from copy import deepcopy
import json
from types import SimpleNamespace
import urllib.error

import pytest

from xui import inbounds
from xui.panel import Panel, PanelError, PanelHTTPError
from xui.templates import vless_reality_tcp_inbound, to_xui_api_payload


@pytest.fixture
def desired():
    return vless_reality_tcp_inbound(port=443, uuid="new-id", private_key="new-private", short_id="aabb",
                                     sni="example.invalid", dest="example.invalid:443")


def stored(desired):
    row = to_xui_api_payload(desired)
    row.update(id=7, up=123, down=456)
    settings = json.loads(row["settings"])
    settings["clients"][0].update(id="original-id", enable=False, totalGB=1024**4, expiryTime=12345)
    settings["clients"].append({"id": "other-id", "email": "other", "totalGB": 999, "expiryTime": 777})
    row["settings"] = json.dumps(settings)
    reality = json.loads(row["streamSettings"])
    reality["realitySettings"]["privateKey"] = "original-private"
    row["streamSettings"] = json.dumps(reality)
    return row


class FakePanel:
    def __init__(self, rows=(), lost_reply=False):
        self.rows = deepcopy(list(rows))
        self.paths = []
        self.lost_reply = lost_reply

    def list_inbounds(self):
        return deepcopy(self.rows)

    def request(self, path, payload):
        self.paths.append(path)
        if path.startswith("/setEnable/"):
            self.rows[0]["enable"] = payload["enable"]
            return
        row = deepcopy(payload)
        row.setdefault("id", 7)
        if path.startswith("/update/") and self.rows:
            # 3.11 ignores client/enable changes on the inbound endpoint.
            row["settings"] = self.rows[0]["settings"]
            row["enable"] = self.rows[0]["enable"]
        self.rows = [row]
        if self.lost_reply:
            self.lost_reply = False
            raise urllib.error.URLError("synthetic lost reply")

    def update_client(self, inbound_id, client):
        row = self.rows[0]
        settings = json.loads(row["settings"])
        settings["clients"] = [deepcopy(client) if old["email"] == client["email"] else old
                               for old in settings["clients"]]
        settings["clients"][0]["updated_at"] = 999999
        if not settings["clients"][0].get("subId"):
            settings["clients"][0]["subId"] = "generated-by-panel"
        row["settings"] = json.dumps(settings)


@pytest.mark.parametrize("existing", [False, True])
def test_repeated_install_preserves_identity_and_other_clients(monkeypatch, desired, existing):
    panel = FakePanel([stored(desired)] if existing else [])
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    first = inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)
    second = inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)
    assert len(panel.rows) == 1
    assert first["settings"] == second["settings"]
    client = second["settings"]["clients"][0]
    assert client["enable"] is True
    assert client["totalGB"] == client["expiryTime"] == 0
    assert client["id"] == ("original-id" if existing else "new-id")
    if existing:
        assert second["up"] == 123 and second["down"] == 456
        assert second["settings"]["clients"][1] == json.loads(stored(desired)["settings"])["clients"][1]
        assert inbounds.connection_values(second)["private_key"] == "original-private"
    assert panel.paths == (["/update/7", "/update/7"] if existing else ["/add", "/update/7"])


def test_lost_add_reply_relists_instead_of_duplicate_add(monkeypatch, desired):
    panel = FakePanel(lost_reply=True)
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    monkeypatch.setattr(inbounds.time, "sleep", lambda *args: None)
    inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)
    assert panel.paths == ["/add", "/update/7"]


@pytest.mark.parametrize("case", ["duplicate", "port", "email", "protocol", "missing_key"])
def test_ambiguous_or_unrelated_inbounds_are_not_modified(monkeypatch, desired, case):
    row = stored(desired)
    if case == "duplicate":
        rows = [row, deepcopy(row)]
    elif case == "port":
        row["remark"] = "unrelated"
        rows = [row]
    elif case == "email":
        row.update(remark="unrelated", port=12345)
        rows = [row]
    elif case == "protocol":
        row["protocol"] = "vmess"
        rows = [row]
    else:
        row["streamSettings"] = json.dumps({"network": "tcp", "security": "reality", "realitySettings": {}})
        rows = [row]
    panel = FakePanel(rows)
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    with pytest.raises(PanelError):
        inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)
    assert panel.paths == []
    assert panel.rows == rows


def test_success_reply_without_saved_configuration_is_failure(monkeypatch, desired):
    panel = FakePanel()
    panel.request = lambda *args: None
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    with pytest.raises(PanelError, match="verified"):
        inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)


@pytest.mark.parametrize("body", [b"<html>login</html>", b'{"success":false,"msg":"private-key-secret"}', b'[]'])
def test_api_never_accepts_invalid_reply_or_leaks_provider_message(body):
    from contextlib import nullcontext
    panel = object.__new__(Panel)
    panel.base, panel.cookie, panel.csrf = "http://127.0.0.1:1234", "synthetic", "synthetic"
    panel.opener = SimpleNamespace(open=lambda *args, **kwargs: nullcontext(SimpleNamespace(read=lambda *args: body)))
    with pytest.raises(PanelError) as caught:
        panel.request("/list")
    assert "private-key-secret" not in str(caught.value)


def test_public_key_matches_private_key():
    import base64
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from xui.utils import reality_public_key
    key = X25519PrivateKey.generate()
    private = base64.urlsafe_b64encode(key.private_bytes_raw()).decode().rstrip("=")
    public = base64.urlsafe_b64encode(key.public_key().public_bytes_raw()).decode().rstrip("=")
    assert reality_public_key(private) == public


def test_disabled_inbound_is_enabled_through_dedicated_endpoint(monkeypatch, desired):
    row = stored(desired)
    row["enable"] = False
    panel = FakePanel([row])
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    saved = inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)
    assert saved["enable"] is True
    assert panel.paths == ["/update/7", "/setEnable/7"]


def test_ignored_client_update_cannot_be_reported_successful(monkeypatch, desired):
    panel = FakePanel([stored(desired)])
    panel.update_client = lambda *args: None
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    with pytest.raises(PanelError, match="client settings"):
        inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)


@pytest.mark.parametrize("code,legacy", [(404, True), (401, False), (403, False)])
def test_legacy_client_route_used_only_when_modern_route_missing(code, legacy):
    panel = object.__new__(Panel)
    calls = []
    def request(path, payload):
        calls.append((path, payload))
        if len(calls) == 1:
            raise PanelHTTPError(code)
    panel.request = request
    client = {"id": "synthetic-id", "email": "test+name", "totalGB": 0, "expiryTime": 0}
    if legacy:
        panel.update_client(7, client)
        assert calls[1][0] == "/updateClient/synthetic-id"
        assert calls[1][1]["id"] == 7
        assert json.loads(calls[1][1]["settings"])["clients"] == [client]
    else:
        with pytest.raises(PanelHTTPError):
            panel.update_client(7, client)
        assert len(calls) == 1
    assert calls[0][0] == "/clients/update/test%2Bname"


@pytest.mark.parametrize("xray_name,expected", [("xray-linux-amd64", True), ("xray-linux-arm64", True), ("nginx", False)])
def test_install_verification_checks_listener_owner(monkeypatch, tmp_path, xray_name, expected):
    import psutil
    from xui import utils
    binary = tmp_path / "x-ui"
    monkeypatch.setattr(utils, "XUI_BINARY_COMMAND", str(binary))
    monkeypatch.setattr(psutil, "net_connections", lambda **kwargs: [
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=54321), pid=1),
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=443), pid=2)])
    paths = {1: str(binary), 2: str(tmp_path / "bin" / xray_name)}
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(exe=lambda: paths[pid]))
    assert utils.installation_listeners_ready(54321, 443) is expected


def test_ignored_enable_cannot_be_hidden_by_payload_defaults(monkeypatch, desired):
    row = stored(desired)
    row["enable"] = False
    panel = FakePanel([row])
    original = panel.request
    def request(path, payload):
        if not path.startswith("/setEnable/"):
            original(path, payload)
    panel.request = request
    monkeypatch.setattr(inbounds, "Panel", lambda *args: panel)
    with pytest.raises(PanelError, match="inbound settings"):
        inbounds.ensure_inbound(1234, "test", "synthetic", "", desired)
