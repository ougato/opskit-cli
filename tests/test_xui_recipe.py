from __future__ import annotations

from software.registry import all_recipes, get
from xui.utils import redact_state, write_secret_json


def test_is_wsl_detects_microsoft_marker(monkeypatch, tmp_path) -> None:
    from xui import utils

    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    osrelease = tmp_path / "osrelease"
    osrelease.write_text("5.15.167.4-microsoft-standard-WSL2\n", encoding="utf-8")
    monkeypatch.setattr(utils, "WSL_OSRELEASE_FILE", osrelease)
    assert utils.is_wsl() is True


def test_is_wsl_false_on_plain_linux(monkeypatch, tmp_path) -> None:
    from xui import utils

    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    osrelease = tmp_path / "osrelease"
    osrelease.write_text("5.15.200\n", encoding="utf-8")
    monkeypatch.setattr(utils, "WSL_OSRELEASE_FILE", osrelease)
    assert utils.is_wsl() is False


def test_is_wsl_true_via_env(monkeypatch) -> None:
    from xui import utils

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert utils.is_wsl() is True


def _make_xui_db(path, rows) -> None:
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            "create table inbounds (id integer primary key, remark text, up integer, down integer)"
        )
        conn.executemany(
            "insert into inbounds(id, remark, up, down) values (?, ?, ?, ?)", rows
        )


def test_human_bytes() -> None:
    from xui.traffic import human_bytes

    assert human_bytes(None) == "—"
    assert human_bytes(0) == "0 B"
    assert human_bytes(512) == "512 B"
    assert human_bytes(1024) == "1.00 KB"
    assert human_bytes(1536) == "1.50 KB"


def test_traffic_snapshot_and_stats(monkeypatch, tmp_path) -> None:
    import sqlite3

    from xui import traffic

    db = tmp_path / "x-ui.db"
    hist = tmp_path / "hist.db"
    _make_xui_db(db, [(1, "JP", 100, 200)])
    monkeypatch.setattr(traffic, "XUI_DATABASE_FILE", db)
    monkeypatch.setattr(traffic, "XUI_TRAFFIC_HISTORY_FILE", hist)

    traffic.take_snapshot()
    with sqlite3.connect(db) as conn:
        conn.execute("update inbounds set up = 350, down = 600 where id = 1")

    stats = traffic.compute_stats()
    assert len(stats) == 1
    node = stats[0]
    assert node["remark"] == "JP"
    assert node["total"] == {"up": 350, "down": 600}
    assert node["today"] == {"up": 250, "down": 400}
    assert node["week"] == {"up": 250, "down": 400}
    assert node["month"] == {"up": 250, "down": 400}


def test_traffic_stats_without_history(monkeypatch, tmp_path) -> None:
    from xui import traffic

    db = tmp_path / "x-ui.db"
    _make_xui_db(db, [(1, "JP", 10, 20)])
    monkeypatch.setattr(traffic, "XUI_DATABASE_FILE", db)
    monkeypatch.setattr(traffic, "XUI_TRAFFIC_HISTORY_FILE", tmp_path / "missing.db")

    stats = traffic.compute_stats()
    assert stats[0]["total"] == {"up": 10, "down": 20}
    assert stats[0]["today"] == {"up": None, "down": None}


def test_traffic_stats_handles_counter_reset(monkeypatch, tmp_path) -> None:
    import sqlite3

    from xui import traffic

    db = tmp_path / "x-ui.db"
    hist = tmp_path / "hist.db"
    _make_xui_db(db, [(1, "JP", 1000, 2000)])
    monkeypatch.setattr(traffic, "XUI_DATABASE_FILE", db)
    monkeypatch.setattr(traffic, "XUI_TRAFFIC_HISTORY_FILE", hist)

    traffic.take_snapshot()
    with sqlite3.connect(db) as conn:
        conn.execute("update inbounds set up = 50, down = 80 where id = 1")

    stats = traffic.compute_stats()
    assert stats[0]["today"] == {"up": 0, "down": 0}


def test_xui_recipe_is_registered_without_submenu() -> None:
    keys = {cls.key for cls in all_recipes()}
    assert "xui" in keys
    assert "xui_server" not in keys
    recipe = get("xui")
    assert recipe is not None
    assert recipe.hidden is False
    assert recipe.has_submenu is False
    assert recipe.has_wizard is True
    assert recipe.has_diagnose is True
    assert recipe.has_manage is True


def test_xui_state_redacts_sensitive_fields_and_uses_0600(tmp_path) -> None:
    target = tmp_path / "server.json"
    state: dict[str, object] = {
        "panel_password": "panel-secret",
        "vless": {"private_key": "reality-private", "public_key": "pub"},
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
                "opskit-vless-reality-tcp",
                json.dumps({"clients": [{"email": "opskit-vless-reality-tcp", "enable": False}]}),
            ),
        )
        conn.execute("insert into clients values (?, 0, 0, 0)", ("opskit-vless-reality-tcp",))
        conn.execute("insert into client_traffics values (1, ?, 0, 0, 0)", ("opskit-vless-reality-tcp",))

    monkeypatch.setattr(utils, "XUI_DATABASE_FILE", db)
    utils.enable_inbound_clients(["opskit-vless-reality-tcp"])

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
