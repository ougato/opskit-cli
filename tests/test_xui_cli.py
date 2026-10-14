import json
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from core.cli import configure
from core.cli_secrets import export_secret
from software.install_config import load_options

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    configure("text", False)
    yield
    configure("text", False)


def test_generated_credentials_require_explicit_export(tmp_path):
    path = tmp_path / "xui.yaml"
    path.write_text("schema_version: 1\nhost: example.invalid\n")
    with pytest.raises(ValueError):
        load_options("xui", str(path))
    path.write_text("schema_version: 1\nhost: example.invalid\ncredentials_output_file: credentials.json\n")
    options = load_options("xui", str(path))
    assert options["panel_password"]
    assert options["credentials_output_file"] == str(tmp_path / "credentials.json")


@pytest.mark.parametrize("extra", ["panel_port: true", "vless_port: 65536", "panel_base_path: '../escape'", "dest: 'host:0'", "unknown: true"])
def test_xui_config_rejected_before_side_effects(tmp_path, extra):
    path = tmp_path / "xui.yaml"
    path.write_text("schema_version: 1\nhost: example.invalid\ncredentials_output_file: output.json\n" + extra)
    with pytest.raises(ValueError):
        load_options("xui", str(path))
    assert not (tmp_path / "output.json").exists()


def test_secret_export_exclusive_and_private(tmp_path):
    import os
    target = tmp_path / "credentials.json"
    export_secret(str(target), {"secret": "synthetic"})
    with pytest.raises(FileExistsError):
        export_secret(str(target), {"secret": "replacement"})
    assert json.loads(target.read_text())["secret"] == "synthetic"
    if os.name != "nt":
        assert target.stat().st_mode & 0o077 == 0


def test_xui_status_redacts_and_links_require_export(tmp_path, monkeypatch):
    import main
    from core import platform
    from xui import utils
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(utils, "load_state", lambda: {"panel_password": "synthetic-password", "vless": {"uuid": "synthetic-id", "link": "vless://synthetic-id@host", "private_key": "synthetic-key"}})
    result = runner.invoke(main.app, ["--output", "json", "xui", "status"])
    assert result.exit_code == 0
    assert "synthetic" not in result.stdout
    target = tmp_path / "links.json"
    exported = runner.invoke(main.app, ["--output", "json", "xui", "links", "--output-file", str(target)])
    assert exported.exit_code == 0
    assert "synthetic" not in exported.stdout
    assert json.loads(target.read_text())["vless_link"].startswith("vless://")


def test_script_install_executes_without_input_and_exports_only_to_file(tmp_path, monkeypatch):
    from xui import server
    from core.sysconfig import SysConfigManager
    configure("json", True)
    def forbidden(*args, **kwargs):
        raise AssertionError("script installation must not prompt")
    monkeypatch.setattr(server, "_read_text", forbidden)
    monkeypatch.setattr(server, "_read_int", forbidden)
    monkeypatch.setattr(server, "confirm", forbidden)
    for name in ("_ensure_linux", "_install_base_packages", "_enable_bbr", "restart_service", "enable_service", "install_traffic_timer"):
        monkeypatch.setattr(server, name, lambda: None)
    monkeypatch.setattr(server, "systemd_available", lambda: True)
    monkeypatch.setattr(server, "command_exists", lambda cmd: True)
    monkeypatch.setattr(server, "generate_reality_keypair", lambda: ("test-private", "test-public"))
    monkeypatch.setattr(server, "configure_panel_settings", lambda **kw: True)
    monkeypatch.setattr(server, "get_panel_settings", lambda: {})
    monkeypatch.setattr(server, "_create_inbounds", lambda **kw: True)
    monkeypatch.setattr(server, "write_secret_json", lambda *args: None)
    monkeypatch.setattr(SysConfigManager, "save", lambda *args, **kw: None)
    monkeypatch.setattr(SysConfigManager, "mark_installed", lambda *args, **kw: None)
    output = tmp_path / "credentials.json"
    options = {"panel_port": 2053, "panel_user": "admin", "panel_password": "synthetic-password", "panel_base_path": "", "host": "example.invalid", "vless_port": 443, "sni": "example.invalid", "dest": "example.invalid:443", "credentials_output_file": str(output)}
    result = server.install_server(options)
    assert result["installed"] and "panel_password" not in result
    assert json.loads(output.read_text())["panel_password"] == "synthetic-password"
