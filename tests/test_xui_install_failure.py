"""Service failures cannot become successful installs in either UI entry path."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from core.cli import configure


@pytest.mark.parametrize("scripted", [False, True], ids=["menu", "script"])
@pytest.mark.parametrize("stage", ["panel_restart", "inbound", "enable", "final_restart", "timer", "verify", None])
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
    monkeypatch.setattr(server, "_create_inbounds", lambda **kwargs: stage != "inbound")
    def verify(*args):
        if stage == "verify":
            raise failure
    monkeypatch.setattr(server, "_verify_installation", verify)
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
        with pytest.raises(server.InstallError if stage == "inbound" else OSError) as caught:
            server.install_server(options if scripted else None)
        if stage != "inbound":
            assert caught.value is failure
        assert not {"state", "installed", "success"}.intersection(events)
        if stage not in {"timer", "verify"}:
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


@pytest.mark.parametrize("service,panel,inbound", [(False, True, True), (True, False, True), (True, True, False)])
def test_verification_rejects_missing_service_or_listener(monkeypatch, service, panel, inbound):
    from xui import server
    monkeypatch.setattr(server, "is_service_active", lambda: service)
    monkeypatch.setattr(server, "installation_listeners_ready", lambda *args: panel and inbound)
    monkeypatch.setattr(server.time, "sleep", lambda delay: None)
    with pytest.raises(server.InstallError):
        server._verify_installation(54321, 443)


def test_verification_waits_for_xray_listener(monkeypatch):
    from xui import server
    attempts = iter([False, True])
    monkeypatch.setattr(server, "is_service_active", lambda: True)
    monkeypatch.setattr(server, "installation_listeners_ready", lambda *args: next(attempts))
    monkeypatch.setattr(server.time, "sleep", lambda delay: None)
    server._verify_installation(54321, 443)


@pytest.mark.parametrize("manager,package", [("apt-get", "iproute2"), ("yum", "iproute")])
def test_installer_adds_listener_tool_when_missing(monkeypatch, manager, package):
    from xui import server
    calls = []
    monkeypatch.setattr(server, "command_exists", lambda command: command in {"curl", "sqlite3", manager})
    monkeypatch.setattr(server, "run_as_root", lambda command, **kwargs: calls.append(command))
    server._install_base_packages()
    assert package in calls[-1]


@pytest.mark.parametrize("panel,vless", [(0, 443), (54321, 65536), (443, 443), (True, 443)])
def test_invalid_ports_fail_before_system_changes(monkeypatch, panel, vless):
    from xui import server
    monkeypatch.setattr(server, "_ensure_linux", lambda: None)
    monkeypatch.setattr(server, "systemd_available", lambda: True)
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must run before installation effects")
    monkeypatch.setattr(server, "_install_base_packages", forbidden)
    options = {"panel_port": panel, "panel_user": "test", "panel_password": "synthetic",
               "panel_base_path": "", "host": "example.invalid", "vless_port": vless,
               "sni": "example.invalid", "dest": "example.invalid:443"}
    with pytest.raises(server.InstallError):
        server.install_server(options)

@pytest.mark.parametrize("lang,label", [("zh", "\u9762\u677f\u7aef\u53e3"), ("en", "Panel port")])
def test_install_prompt_keeps_default_on_input_line(monkeypatch, capsys, lang, label):
    from core import i18n, prompt
    from xui import server

    configure("text", False)
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(lang)))
    monkeypatch.setattr(prompt.os, "system", lambda command: 0)
    monkeypatch.setattr(prompt, "_read_line", lambda **kwargs: "")

    assert server._read_int(["OpsKit"], "xui.input.panel_port", 54321) == 54321
    output = capsys.readouterr().out
    assert "{default}" not in output
    assert "54321" not in next(line for line in output.splitlines() if label in line)
    assert output.count("54321") == 1
    assert output.rstrip().endswith(" 54321")


def test_esc_returns_from_install_wizard_without_failure(monkeypatch):
    from core import prompt
    from software import menu, resolver
    from software.recipes.xui.recipe import XuiRecipe

    configure("text", False)
    monkeypatch.setattr(resolver, "resolve_deps", lambda *args: None)
    monkeypatch.setattr(XuiRecipe, "detect", lambda self: None)
    monkeypatch.setattr(menu, "clear_screen", lambda: None)
    monkeypatch.setattr(prompt.os, "system", lambda command: 0)
    monkeypatch.setattr(menu, "report_failure", lambda *args, **kwargs: pytest.fail("Esc reported as a failure"))
    monkeypatch.setattr(menu, "pause", lambda: pytest.fail("Esc caused a pause"))

    def cancel(**kwargs):
        raise prompt.UserCancel

    monkeypatch.setattr(prompt, "_read_line", cancel)
    menu._do_install(["OpsKit"], XuiRecipe, XuiRecipe())


def test_upstream_install_keeps_script_output_hidden(monkeypatch, capsys):
    from core import process
    from xui import utils

    stages = []
    monkeypatch.setattr(utils, "get_bytes", lambda *a, **k: b"script")
    monkeypatch.setattr(utils, "root_command", list)

    def installer(command, **kwargs):
        assert kwargs["stdin_data"] == b"\n"
        assert kwargs["timeout"] > 0
        kwargs["checkpoint"]()
        yield "Got x-ui latest version: v1, beginning the installation..."
        yield "Password: upstream-password"
        yield "Checksum verified: digest"
        yield "Setting up systemd unit..."
        return 0

    monkeypatch.setattr(process, "stream", installer)
    utils.install_xui_script(checkpoint=lambda: stages.append("checkpoint"))
    assert stages == ["checkpoint"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("valid_ip", [b"8.8.8.8\n", b"2606:4700::1111", b""])
def test_public_host_probe_ignores_html(monkeypatch, valid_ip):
    from io import BytesIO
    from xui import utils

    responses = {
        "https://api.ip.sb/ip": b"<!doctype html><title>IP.SB</title>",
        "https://api.ipify.org": valid_ip,
    }
    monkeypatch.setattr(utils, "PUBLIC_IP_APIS", list(responses))
    monkeypatch.setattr(utils.urllib.request, "urlopen", lambda req, **kwargs: BytesIO(responses[req.full_url]))
    assert utils.detect_public_host() == valid_ip.decode().strip()
