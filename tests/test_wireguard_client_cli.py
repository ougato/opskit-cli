import copy
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from core.cli import configure


@pytest.fixture
def environment(monkeypatch):
    from wireguard import client, utils, token
    from core import platform, privilege, prompt, theme
    from wireguard.constants import WG_CLIENT_STATE_FILE
    state = {"tunnels": [{"label": "hk", "wg_iface": "wg-hk", "xray_svc": "xray@hk", "local_port": 4000, "token": "old-secret"}]}
    paths = {"/usr/local/etc/xray/hk.json": "old-xray", "/etc/wireguard/wg-hk.conf": "old-wg", WG_CLIENT_STATE_FILE: json.dumps(state)}
    original = copy.deepcopy(paths)
    calls = []
    def root(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    def save(value):
        state.clear()
        state.update(copy.deepcopy(value))
        paths[WG_CLIENT_STATE_FILE] = json.dumps(value)
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(client, "_load_client_state", lambda **kwargs: copy.deepcopy(state))
    monkeypatch.setattr(client, "_save_client_state", save)
    monkeypatch.setattr(client, "run_as_root", root)
    monkeypatch.setattr(privilege, "read_root_file", lambda path: paths.get(path, ""))
    monkeypatch.setattr(privilege, "unix_file_nonempty", lambda path, **kwargs: bool(paths.get(path)))
    monkeypatch.setattr(utils, "write_file", lambda path, content: paths.__setitem__(path, content))
    monkeypatch.setattr(utils, "write_secret_file", lambda path, content: paths.__setitem__(path, content))
    monkeypatch.setattr(utils, "enable_and_start", lambda service: None)
    monkeypatch.setattr(utils, "stop_and_disable", lambda service: None)
    monkeypatch.setattr(utils, "is_service_active", lambda service, **kwargs: True)
    monkeypatch.setattr(client, "_has_local_dns_service", lambda: True)
    monkeypatch.setattr(client, "_remove_wg_watchdog", lambda *a, **k: None)
    monkeypatch.setattr(prompt, "clear_screen", lambda: None)
    monkeypatch.setattr(theme, "print_action_title", lambda *a: None)
    monkeypatch.setattr(token, "decode_token", lambda raw: {
        "server_ip": "192.0.2.1", "server_port": 443,
        "wg_server_pubkey": "pub", "wg_client_privkey": "priv", "wg_psk": "psk",
        "client_ip": "10.10.10.2", "uuid": "synthetic-uuid", "sni": "example.invalid",
    })
    import time
    monkeypatch.setattr(time, "sleep", lambda _: None)
    yield SimpleNamespace(state=state, paths=paths, original=original, calls=calls)
    configure("text", False)


def invoke(*args):
    import main
    return CliRunner().invoke(main.app, ["--output", "json", *args])


def test_update_cli_uses_stable_id_and_no_secret_output(environment, tmp_path):
    source = tmp_path / "token"
    source.write_text("new-secret")
    result = invoke("wireguard", "client", "tunnel", "update-token", "wg-hk", "--token-file", str(source))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"id": "wg-hk", "updated": True}
    assert "new-secret" not in result.output
    assert environment.state["tunnels"][0]["token"] == "new-secret"


def test_unhealthy_update_restores_config_and_does_not_commit(environment, monkeypatch):
    from wireguard import client, utils
    monkeypatch.setattr(utils, "is_service_active", lambda _, **kwargs: False)
    with pytest.raises(RuntimeError, match="health check"):
        client.update_client_token([], tunnel_id="wg-hk", token="new-secret")
    assert environment.paths == environment.original
    assert environment.state["tunnels"][0]["token"] == "old-secret"
    assert ["systemctl", "restart", "xray@hk"] in environment.calls


def test_partial_state_write_failure_restores_persisted_state(environment, monkeypatch):
    from wireguard import client
    from wireguard.constants import WG_CLIENT_STATE_FILE
    def fail_save(value):
        environment.paths[WG_CLIENT_STATE_FILE] = "partial-new-state"
        raise OSError("synthetic write failure")
    monkeypatch.setattr(client, "_save_client_state", fail_save)
    with pytest.raises(OSError):
        client.update_client_token([], tunnel_id="wg-hk", token="new-secret")
    assert environment.paths == environment.original


@pytest.mark.parametrize("field,value", [("label", "../../etc"), ("wg_iface", "../../etc"), ("xray_svc", "other-service")])
def test_remove_rejects_tainted_state_before_root_operation(environment, field, value):
    from wireguard import client
    environment.state["tunnels"][0][field] = value
    with pytest.raises(ValueError):
        client.remove_tunnel([], tunnel_id=environment.state["tunnels"][0]["wg_iface"])
    assert not environment.calls


def test_list_excludes_token_and_uuid(environment):
    environment.state["tunnels"][0]["uuid"] = "private-uuid"
    result = invoke("wireguard", "client", "tunnel", "list")
    assert result.exit_code == 0
    assert "old-secret" not in result.stdout and "private-uuid" not in result.stdout
    assert json.loads(result.stdout)["data"][0]["id"] == "wg-hk"


def test_invalid_explicit_install_token_raises_instead_of_success(monkeypatch):
    from wireguard import client, token
    monkeypatch.setattr(token, "decode_token", lambda _: (_ for _ in ()).throw(ValueError("secret-value")))
    with pytest.raises(ValueError, match="invalid WireGuard token"):
        client.install_client(token="synthetic-bad-token")


def test_missing_id_is_usage_error(environment, tmp_path):
    source = tmp_path / "token"
    source.write_text("new-secret")
    result = invoke("wireguard", "client", "tunnel", "update-token", "unknown", "--token-file", str(source))
    assert result.exit_code == 2
    assert not environment.calls


@pytest.mark.parametrize("failure", ["health", "start", "state", None])
def test_install_commits_only_healthy_tunnel_and_cleans_failures(environment, monkeypatch, failure):
    from wireguard import client, utils
    from core.sysconfig import SysConfigManager
    import socket
    import subprocess
    marked = []
    monkeypatch.setattr(utils, "detect_default_iface", lambda: "eth0")
    monkeypatch.setattr(utils, "get_os_id", lambda: "ubuntu")
    monkeypatch.setattr(utils, "install_wireguard_pkg", lambda _: None)
    monkeypatch.setattr(utils, "install_xray", lambda: None)
    monkeypatch.setattr(client, "_alloc_local_port", lambda _: 4001)
    monkeypatch.setattr(client, "_ensure_xray_template_service", lambda _: None)
    monkeypatch.setattr(client, "_ensure_wg_watchdog", lambda _: None)
    monkeypatch.setattr(client, "write_root_file", lambda path, content, mode: environment.paths.__setitem__(path.as_posix(), content))
    monkeypatch.setattr(SysConfigManager, "save", lambda *a, **k: None)
    monkeypatch.setattr(SysConfigManager, "restore", lambda *a, **k: None)
    monkeypatch.setattr(SysConfigManager, "remove", lambda *a, **k: None)
    monkeypatch.setattr(SysConfigManager, "mark_installed", lambda key: marked.append(key))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    def root(args, **kwargs):
        environment.calls.append(args)
        if args[0] == "test":
            return SimpleNamespace(returncode=1)
        if args[:2] == ["rm", "-f"]:
            for path in args[2:]:
                environment.paths.pop(path, None)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(client, "run_as_root", root)
    if failure == "health":
        monkeypatch.setattr(utils, "is_service_active", lambda _, **kwargs: False)
    elif failure == "start":
        monkeypatch.setattr(utils, "enable_and_start", lambda _: (_ for _ in ()).throw(RuntimeError("start failed")))
    elif failure == "state":
        monkeypatch.setattr(client, "_save_client_state", lambda _: (_ for _ in ()).throw(OSError("state write failed")))
    if failure:
        with pytest.raises((OSError, RuntimeError)):
            client.install_client(token="new-secret")
        assert environment.paths == environment.original
        assert len(environment.state["tunnels"]) == 1
        assert ["systemctl", "stop", "wg-quick@wg-default", "xray@default"] in environment.calls
        if failure != "state":
            assert not marked
    else:
        result = client.install_client(token="new-secret")
        assert result == {"id": "wg-default", "label": "default", "installed": True}
        assert len(environment.state["tunnels"]) == 2
        assert marked == ["wg_client_default"]


def test_whole_uninstall_rejects_tainted_later_tunnel_before_effects(environment):
    from wireguard import client
    environment.state["tunnels"].append({"label": "../../", "wg_iface": "wg-evil", "xray_svc": "xray@evil"})
    with pytest.raises(ValueError):
        client.uninstall_client()
    assert not environment.calls


@pytest.mark.parametrize("content", ["broken-json", "[]", '{"tunnels": [1]}'])
def test_invalid_state_cannot_be_treated_as_empty(monkeypatch, content):
    from core import privilege
    from wireguard import client
    monkeypatch.setattr(privilege, "read_root_file", lambda _: content)
    with pytest.raises(ValueError, match="invalid WireGuard client state"):
        client._load_client_state()


def test_install_refuses_existing_config_before_touching_services(environment):
    from wireguard import client
    with pytest.raises(RuntimeError, match="overwritten"):
        with client._client_install_transaction("hk", last_tunnel=False):
            pytest.fail("collision must be rejected")
    assert environment.paths == environment.original
    assert all(call[0] == "test" for call in environment.calls)


def test_update_reports_incomplete_rollback(environment, monkeypatch):
    from wireguard import client, utils
    monkeypatch.setattr(utils, "is_service_active", lambda _, **kwargs: False)
    def writer(path, content):
        if content == "old-wg":
            raise OSError("cannot restore")
        environment.paths[path] = content
    monkeypatch.setattr(utils, "write_secret_file", writer)
    with pytest.raises(RuntimeError, match="rollback incomplete"):
        client.update_client_token([], tunnel_id="wg-hk", token="new-secret")
    assert environment.state["tunnels"][0]["token"] == "old-secret"


def test_update_stop_failure_cannot_be_reported_success(environment, monkeypatch):
    from wireguard import client
    def root(args, **kwargs):
        environment.calls.append(args)
        if args[:2] == ["systemctl", "stop"]:
            raise RuntimeError("service stop failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(client, "run_as_root", root)
    with pytest.raises(RuntimeError, match="stop failed"):
        client.update_client_token([], tunnel_id="wg-hk", token="new-secret")
    assert environment.paths == environment.original
    assert environment.state["tunnels"][0]["token"] == "old-secret"


def test_remove_cli_requires_yes_and_preserves_other_tunnels(environment, monkeypatch):
    from core.sysconfig import SysConfigManager
    monkeypatch.setattr(SysConfigManager, "restore", lambda _: None)
    monkeypatch.setattr(SysConfigManager, "remove", lambda _: None)
    environment.state["tunnels"].append({"label": "us", "wg_iface": "wg-us", "xray_svc": "xray@us"})
    refused = invoke("wireguard", "client", "tunnel", "remove", "wg-hk")
    assert refused.exit_code != 0 and not environment.calls
    result = invoke("--yes", "wireguard", "client", "tunnel", "remove", "wg-hk")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"id": "wg-hk", "removed": True}
    assert environment.state["tunnels"] == [{"label": "us", "wg_iface": "wg-us", "xray_svc": "xray@us"}]
