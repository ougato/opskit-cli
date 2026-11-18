"""Official reports are read-only and unhealthy reports are not successes."""
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import main
from core import platform
from software import registry


@pytest.mark.parametrize("name", ["rustdesk", "tailscale", "xui", "wg_server", "wg_client"])
@pytest.mark.parametrize("healthy", [True, False])
def test_software_diagnose_uses_report_without_boot_or_menu(monkeypatch, name, healthy):
    cls = registry.get(name)
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("diagnostics must not boot or apply updates"))
    monkeypatch.setattr(cls, "diagnose", lambda self: pytest.fail("must not render legacy menu"))
    monkeypatch.setattr(cls, "diagnostic_report", lambda self, **kwargs: {"checks": [{"name": "service", "ok": healthy}]})
    result = CliRunner().invoke(main.app, ["--output", "json", "software", "diagnose", name])
    assert result.exit_code == (0 if healthy else 1), result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is healthy
    assert payload["data"]["checks"] == [{"name": "service", "ok": healthy}]
    assert payload["error"] is None if healthy else payload["error"]["code"] == 1


def test_rustdesk_actual_report_does_not_read_or_export_secrets(tmp_path, monkeypatch):
    from software.recipes.rustdesk import server
    install = tmp_path / "bin"
    install.mkdir()
    for binary in server.RUSTDESK_BINARIES:
        (install / binary).write_text("test executable")
    key = tmp_path / "key"
    key.write_text("synthetic-secret-key")
    state = tmp_path / "state.json"
    state.write_text('{"key":"synthetic-secret-key"}')
    monkeypatch.setattr(server, "RUSTDESK_INSTALL_DIR", install)
    monkeypatch.setattr(server, "RUSTDESK_KEY_FILE", key)
    monkeypatch.setattr(server, "RUSTDESK_STATE_FILE", state)
    monkeypatch.setattr(server, "is_process_active", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.diagnostics.listener_checks", lambda expected, **kwargs: [
        {"name": f"{protocol}:{port}", "ok": True} for protocol, port, _ in expected])
    for operation in ("save_state", "load_state", "read_key", "detect_public_host", "detect_version"):
        monkeypatch.setattr(server, operation, lambda *args: pytest.fail("diagnostic must not mutate/export or contact network"))
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (state, key)}
    result = CliRunner().invoke(main.app, ["--output", "json", "software", "diagnose", "rustdesk"])
    assert result.exit_code == 0, result.output
    assert "synthetic-secret" not in result.output
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (state, key)}
    assert all(check["ok"] for check in json.loads(result.stdout)["data"]["checks"])


def test_wireguard_report_validates_all_identifiers_before_system_queries(monkeypatch):
    from wireguard import client, diagnostics, utils
    monkeypatch.setattr(client, "_load_client_state", lambda **kwargs: {"tunnels": [
        {"label": "good", "wg_iface": "wg-good", "xray_svc": "xray@good"},
        {"label": "../bad", "wg_iface": "wg-bad", "xray_svc": "xray@bad"},
    ]})
    monkeypatch.setattr(utils, "is_service_active", lambda *args: pytest.fail("must validate before system query"))
    with pytest.raises(ValueError):
        diagnostics.client_report()


def test_empty_report_is_not_success(monkeypatch):
    cls = registry.get("rustdesk")
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(cls, "diagnostic_report", lambda self, **kwargs: {"checks": []})
    result = CliRunner().invoke(main.app, ["--output", "json", "software", "diagnose", "rustdesk"])
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]
