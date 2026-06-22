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


def test_enable_inbound_clients_updates_traffic_tables(tmp_path, monkeypatch) -> None:
    import json
    import sqlite3

    from xui import utils

    db = tmp_path / "x-ui.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table inbounds (id integer primary key, remark text, settings text)")
        conn.execute("create table clients (email text, enable numeric, total_gb integer, expiry_time integer)")
        conn.execute(
            "create table client_traffics (inbound_id integer, email text, enable numeric, total integer, expiry_time integer)"
        )
        conn.execute(
            "insert into inbounds values (1, ?, ?)",
            (
                "opskit-vless-reality-xhttp",
                json.dumps({"clients": [{"email": "opskit-vless-reality-xhttp", "enable": False}]}),
            ),
        )
        conn.execute("insert into clients values (?, 0, 0, 0)", ("opskit-vless-reality-xhttp",))
        conn.execute("insert into client_traffics values (1, ?, 0, 0, 0)", ("opskit-vless-reality-xhttp",))

    monkeypatch.setattr(utils, "XUI_DATABASE_FILE", db)
    utils.enable_inbound_clients(["opskit-vless-reality-xhttp"])

    with sqlite3.connect(db) as conn:
        settings = json.loads(conn.execute("select settings from inbounds where id = 1").fetchone()[0])
        client = conn.execute("select enable, total_gb, expiry_time from clients").fetchone()
        traffic = conn.execute("select enable, total, expiry_time from client_traffics").fetchone()
    assert settings["clients"][0]["enable"] is True
    assert settings["clients"][0]["totalGB"] > 0
    assert client[0] == 1
    assert client[1] > 0
    assert client[2] > 0
    assert traffic[0] == 1
    assert traffic[1] > 0
    assert traffic[2] > 0


def test_remove_xui_artifacts_removes_service_and_state(tmp_path, monkeypatch) -> None:
    from xui import utils

    install_dir = tmp_path / "usr-local-x-ui"
    config_dir = tmp_path / "etc-x-ui"
    service_file = tmp_path / "x-ui.service"
    command_link = tmp_path / "x-ui"
    install_dir.mkdir()
    config_dir.mkdir()
    service_file.write_text("service", encoding="utf-8")
    command_link.write_text("cmd", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(utils, "XUI_ARTIFACT_DIRS", [install_dir, config_dir])
    monkeypatch.setattr(utils, "XUI_ARTIFACT_FILES", [service_file, command_link])
    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    utils.remove_xui_artifacts()

    assert not install_dir.exists()
    assert not config_dir.exists()
    assert not service_file.exists()
    assert not command_link.exists()
    assert calls == [["systemctl", "daemon-reload"]]
