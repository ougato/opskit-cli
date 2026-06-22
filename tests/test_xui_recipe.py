from __future__ import annotations

from software.registry import all_recipes, get
from xui.utils import redact_state, write_secret_json


def test_xui_recipes_are_registered() -> None:
    keys = {cls.key for cls in all_recipes()}
    assert "xui" in keys
    assert "xui_server" in keys
    parent = get("xui")
    child = get("xui_server")
    assert parent is not None
    assert child is not None
    assert parent.has_submenu is True
    assert child.hidden is True
    assert child.has_diagnose is True
    assert child.has_manage is True


def test_xui_parent_exposes_server_submenu() -> None:
    recipe_cls = get("xui")
    assert recipe_cls is not None
    items = recipe_cls().submenu_items()
    assert items == [{"key": "xui_server", "label_key": "software.xui_server"}]


def test_xui_state_redacts_sensitive_fields_and_uses_0600(tmp_path) -> None:
    target = tmp_path / "server.json"
    state: dict[str, object] = {
        "panel_password": "panel-secret",
        "vless": {"private_key": "reality-private", "public_key": "pub"},
        "trojan": {"password": "trojan-secret"},
    }
    write_secret_json(target, state)
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    redacted = redact_state(state)
    assert isinstance(redacted, dict)
    assert redacted["panel_password"] == "<redacted>"
    vless = redacted["vless"]
    assert isinstance(vless, dict)
    assert vless["private_key"] == "<redacted>"
    assert vless["public_key"] == "pub"


def test_generate_reality_keypair_uses_3x_ui_xray_binary(tmp_path, monkeypatch) -> None:
    xray = tmp_path / "xray-linux-amd64"
    xray.write_text(
        "#!/bin/sh\n"
        "echo 'PrivateKey: private-key'\n"
        "echo 'Password (PublicKey): public-key'\n",
        encoding="utf-8",
    )
    xray.chmod(0o755)

    from xui import utils

    monkeypatch.setattr(utils, "XUI_XRAY_CANDIDATES", [str(xray)])
    assert utils.generate_reality_keypair() == ("private-key", "public-key")


def test_panel_api_base_normalizes_trailing_slash() -> None:
    from xui.utils import panel_api_base

    assert panel_api_base(54321, "") == "http://127.0.0.1:54321"
    assert panel_api_base(54321, "abc/") == "http://127.0.0.1:54321/abc"
    assert panel_api_base(54321, "/abc/") == "http://127.0.0.1:54321/abc"


def test_configure_panel_settings_uses_quiet_xui_binary(monkeypatch) -> None:
    from xui import utils

    calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(utils, "command_exists", lambda name: True)
    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    assert utils.configure_panel_settings(
        port=54321,
        username="opskit",
        password="secret",
        base_path="/panel/",
    ) is True
    call = calls[0]
    assert call["command"] == [
        "/usr/local/x-ui/x-ui",
        "setting",
        "-username",
        "opskit",
        "-password",
        "secret",
        "-port",
        "54321",
        "-webBasePath",
        "/panel/",
    ]
    assert call["capture_output"] is True
    assert call["text"] is True


def test_extract_csrf_token_from_login_page() -> None:
    from xui.utils import _extract_csrf_token

    html = '<meta name="csrf-token" content="token-value">'
    assert _extract_csrf_token(html) == "token-value"
