"""Service failures cannot become successful installs in either UI entry path."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from core.cli import configure


@pytest.mark.parametrize("scripted", [False, True], ids=["menu", "script"])
@pytest.mark.parametrize("stage", ["panel_restart", "enable", "final_restart", "timer", None])
def test_service_failure_never_publishes_installed_state(tmp_path, monkeypatch, scripted, stage):
    from core.sysconfig import SysConfigManager
    from xui import server

    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    configure("text", False)
    events = []
    for name in ("_ensure_linux", "_install_base_packages", "_enable_bbr", "clear_screen", "print_action_title", "pause"):
        monkeypatch.setattr(server, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "MultiStepProgress", lambda *args: nullcontext(SimpleNamespace(step=lambda *args: None)))
    monkeypatch.setattr(server, "_read_int", lambda breadcrumb, key, default: default)
    monkeypatch.setattr(server, "_read_text", lambda breadcrumb, key, default, **kwargs: default)
    monkeypatch.setattr(server, "detect_public_host", lambda: "example.invalid")
    monkeypatch.setattr(server, "confirm", lambda **kwargs: True)
    monkeypatch.setattr(server, "_precheck_systemd", lambda *args: True)
    monkeypatch.setattr(server, "systemd_available", lambda: True)
    monkeypatch.setattr(server, "command_exists", lambda cmd: True)
    monkeypatch.setattr(server, "generate_reality_keypair", lambda: ("test-private", "test-public"))
    monkeypatch.setattr(server, "configure_panel_settings", lambda **kwargs: True)
    monkeypatch.setattr(server, "get_panel_settings", lambda: {})
    monkeypatch.setattr(server, "_create_inbounds", lambda **kwargs: True)
    monkeypatch.setattr(server, "write_secret_json", lambda *args: events.append("state"))
    monkeypatch.setattr(server, "print_success", lambda *args: events.append("success"))
    monkeypatch.setattr(SysConfigManager, "save", lambda *args, **kwargs: None)
    monkeypatch.setattr(SysConfigManager, "mark_installed", lambda *args: events.append("installed"))
    failure = OSError("synthetic service failure")

    def restart():
        name = "final_restart" if "panel_restart" in events else "panel_restart"
        events.append(name)
        if stage == name:
            raise failure

    def enable():
        events.append("enable")
        if stage == "enable":
            raise failure

    def timer():
        events.append("timer")
        if stage == "timer":
            raise failure

    monkeypatch.setattr(server, "restart_service", restart)
    monkeypatch.setattr(server, "enable_service", enable)
    monkeypatch.setattr(server, "install_traffic_timer", timer)
    output = tmp_path / "credentials.json"
    options = {"panel_port": 2053, "panel_user": "admin", "panel_password": "synthetic-password",
               "panel_base_path": "", "host": "example.invalid", "vless_port": 443,
               "sni": "example.invalid", "dest": "example.invalid:443", "credentials_output_file": str(output)}
    if stage is not None:
        with pytest.raises(OSError) as caught:
            server.install_server(options if scripted else None)
        assert caught.value is failure
        assert not {"state", "installed", "success"}.intersection(events)
        if stage != "timer":
            assert "timer" not in events
        assert not output.exists()
        if stage == "panel_restart":
            assert "enable" not in events
        if stage == "enable":
            assert "final_restart" not in events
    else:
        result = server.install_server(options if scripted else None)
        assert events[:3] == ["panel_restart", "enable", "final_restart"]
        assert {"timer", "state", "installed"}.issubset(events)
        assert output.exists() is scripted
        if scripted:
            assert result["installed"] is True
        else:
            assert "success" in events


@pytest.mark.parametrize("stage", ["enable", "timer_reload", "timer_enable"])
def test_systemctl_nonzero_is_an_install_failure(monkeypatch, stage):
    import subprocess
    import sys
    from xui import utils

    monkeypatch.setattr(utils, "systemd_available", lambda: True)
    monkeypatch.setattr(utils, "write_root_file", lambda *args: None)
    commands = []

    def root(command, **kwargs):
        commands.append(command)
        code = 7 if stage != "timer_enable" or len(commands) == 2 else 0
        return subprocess.run([sys.executable, "-c", f"raise SystemExit({code})"], **kwargs)

    monkeypatch.setattr(utils, "run_as_root", root)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        if stage == "enable":
            utils.enable_service()
        else:
            utils.install_traffic_timer()
    assert caught.value.returncode == 7
    assert len(commands) == (2 if stage == "timer_enable" else 1)
