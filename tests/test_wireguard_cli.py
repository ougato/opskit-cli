import copy
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner
from core.cli import configure
from software.install_config import load_options

runner = CliRunner()


@pytest.fixture
def state(monkeypatch):
    from wireguard import server, utils
    from core import platform
    value = {"vpn_gateway": "10.10.10.1", "clients": [{"pubkey": "stable-key", "name": "old", "ip": "10.10.10.2", "token": "synthetic-secret"}]}
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(server, "_load_state", lambda: copy.deepcopy(value))
    def save(new):
        value.clear()
        value.update(copy.deepcopy(new))
    monkeypatch.setattr(server, "_save_state", save)
    monkeypatch.setattr(server, "run_as_root", lambda *args, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(utils, "gen_wg_keypair", lambda: ("private", "new-key"))
    monkeypatch.setattr(utils, "gen_wg_psk", lambda: "psk")
    monkeypatch.setattr(server, "_generate_client_token", lambda **kw: "new-synthetic-secret")
    yield value
    configure("text", False)


def invoke(*args):
    import main
    return runner.invoke(main.app, ["--output", "json", *args])


def test_peer_identity_rename_export_remove(state, tmp_path):
    result = invoke("wireguard", "server", "peer", "list")
    assert result.exit_code == 0 and "synthetic-secret" not in result.stdout
    renamed = invoke("wireguard", "server", "peer", "rename", "stable-key", "--name", "new")
    assert renamed.exit_code == 0
    assert state["clients"][0]["pubkey"] == "stable-key"
    target = tmp_path / "token.json"
    exported = invoke("wireguard", "server", "peer", "export", "stable-key", "--output-file", str(target))
    assert exported.exit_code == 0 and "synthetic-secret" not in exported.stdout
    assert json.loads(target.read_text())["token"] == "synthetic-secret"
    removed = invoke("--yes", "wireguard", "server", "peer", "remove", "stable-key")
    assert removed.exit_code == 0 and not state["clients"]


def test_peer_add_never_prompts_or_prints_token(state, monkeypatch):
    from core import prompt
    def forbidden(*args, **kw):
        raise AssertionError("unexpected prompt")
    monkeypatch.setattr(prompt, "text_input", forbidden)
    result = invoke("wireguard", "server", "peer", "add", "--name", "second")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["id"] == "new-key"
    assert "new-synthetic-secret" not in result.stdout


def test_peer_apply_failure_does_not_report_success(state, monkeypatch):
    from wireguard import server
    monkeypatch.setattr(server, "run_as_root", lambda *args, **kw: SimpleNamespace(returncode=1, stdout="", stderr="secret-detail"))
    result = invoke("wireguard", "server", "peer", "add", "--name", "second")
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]
    assert len(state["clients"]) == 1
    assert "secret-detail" not in result.stdout


def test_server_config_and_port_conflict_before_mutation(tmp_path, monkeypatch):
    from wireguard import server
    from core import platform, config
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    path = tmp_path / "install.yaml"
    path.write_text("schema_version: 1\ndomain: wg.example.invalid\npublic_ip: 192.0.2.1\ncredentials_output_file: token.json\n")
    options = load_options("wg_server", str(path))
    assert options["subnet_octet"] >= 1
    monkeypatch.setattr(server, "_check_port_443", lambda: ("other", "existing"))
    def forbidden(*args, **kw):
        raise AssertionError("config must not change on conflict")
    monkeypatch.setattr(config, "set_config_value", forbidden)
    with pytest.raises(Exception, match="port 443"):
        server.install_server(options)
    assert not (tmp_path / "token.json").exists()
