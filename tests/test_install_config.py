import json
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from core.cli import configure
from software.install_config import load_options

runner = CliRunner()

@pytest.fixture(autouse=True)
def reset():
    configure("text", False)
    yield
    configure("text", False)

@pytest.mark.parametrize("content", ["schema_version: 1\nwith_p10k: true\n", "schema_version: 1\nwith_p10k: true\nswitch_shell: nope\n", "schema_version: true\nwith_p10k: true\nswitch_shell: false\n", "schema_version: 1\nwith_p10k: true\nswitch_shell: false\nextra: true\n"])
def test_config_rejects_missing_unknown_and_wrong_type(tmp_path, content):
    path = tmp_path / "install.yaml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_options("ohmyzsh", str(path))


def test_config_install_routes_explicit_options(tmp_path, monkeypatch):
    import main
    from software import install_config
    path = tmp_path / "install.yaml"
    path.write_text("schema_version: 1\nwith_p10k: false\nswitch_shell: false\n")
    monkeypatch.setattr(main, "_get_recipe_for_direct", lambda name: (object, object()))
    monkeypatch.setattr(main, "_resolve_deps_or_exit", lambda *args: None)
    monkeypatch.setattr(main, "_check_disk_or_exit", lambda: None)
    seen = []
    def install(name, options):
        seen.append(options)
        print("diagnostic")
        return {"installed": True}
    monkeypatch.setattr(install_config, "install", install)
    result = runner.invoke(main.app, ["--output", "json", "software", "install", "ohmyzsh", "--config", str(path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["installed"]
    assert "diagnostic" in result.stderr
    assert seen == [{"with_p10k": False, "switch_shell": False}]


def test_tailscale_preparation_does_not_login_or_enable_exit(monkeypatch):
    from tailscale import server
    monkeypatch.setattr(server, "_ensure_supported", lambda: None)
    monkeypatch.setattr(server, "command_exists", lambda cmd: True)
    monkeypatch.setattr(server, "_start_service", lambda: None)
    monkeypatch.setattr(server, "detect_tailscale_version", lambda: "test")
    monkeypatch.setattr(server, "is_service_active", lambda: True)
    def forbidden(*args, **kwargs):
        raise AssertionError("implicit permission or login")
    monkeypatch.setattr(server, "configure_exit_node", forbidden)
    monkeypatch.setattr(server, "obtain_auth_url", forbidden)
    assert server.prepare_client()["login_started"] is False


def test_tailscale_login_defaults_to_no_exit_advertisement(monkeypatch):
    import main
    from tailscale import server
    monkeypatch.setattr(server, "_ensure_supported", lambda: None)
    monkeypatch.setattr(server, "command_exists", lambda cmd: True)
    seen = []
    monkeypatch.setattr(server, "start_login", lambda **kw: seen.append(kw) or "")
    monkeypatch.setattr(server, "_status_json", lambda **kw: {"AuthURL": "https://example.invalid/auth"})
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login"])
    assert result.exit_code == 0
    assert seen[0]["advertise_exit_node"] is False
    assert 0 < seen[0]["timeout"] <= server.TAILSCALE_UP_TIMEOUT_SECONDS
    assert json.loads(result.stdout)["data"]["logged_in"] is False
