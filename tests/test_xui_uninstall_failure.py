"""Uninstall refuses unconfirmed service/file removal and keeps recovery metadata."""
from contextlib import nullcontext
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("action,fail_at", [(action, index)
    for action, count in [("service", 3), ("timer", 5), ("artifacts", 3)]
    for index in range(1, count + 1)])
def test_nonzero_stops_uninstall_at_failed_command(tmp_path, monkeypatch, action, fail_at):
    from xui import utils
    monkeypatch.setattr(utils, "systemd_available", lambda: True)
    monkeypatch.setattr(utils, "XUI_ARTIFACT_DIRS", [tmp_path / "program"])
    monkeypatch.setattr(utils, "XUI_ARTIFACT_FILES", [tmp_path / "service"])
    calls = []

    def root(command, **kwargs):
        calls.append(command)
        if len(calls) == fail_at:
            return subprocess.run([sys.executable, "-c", "raise SystemExit(7)"], **kwargs)
        return SimpleNamespace(returncode=0, stdout="loaded\n")

    monkeypatch.setattr(utils, "run_as_root", root)
    function = {"service": utils.stop_and_disable_service, "timer": utils.uninstall_traffic_timer,
                "artifacts": utils.remove_xui_artifacts}[action]
    with pytest.raises(subprocess.CalledProcessError) as caught:
        function()
    assert caught.value.returncode == 7 and len(calls) == fail_at


@pytest.mark.parametrize("load_state", ["not-found", "loaded", "masked", "error", "", "loaded\nnot-found"])
def test_service_absence_is_explicit_and_unknown_state_is_refused(monkeypatch, load_state):
    from xui import utils
    monkeypatch.setattr(utils, "systemd_available", lambda: True)
    calls = []

    def root(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=load_state + "\n")

    monkeypatch.setattr(utils, "run_as_root", root)
    if load_state in {"not-found", "loaded", "masked"}:
        utils.stop_and_disable_service()
        assert len(calls) == (1 if load_state == "not-found" else 3)
    else:
        with pytest.raises(RuntimeError):
            utils.stop_and_disable_service()
        assert len(calls) == 1


@pytest.mark.parametrize("stage", ["service", "timer", "files", "sysctl", None])
def test_uninstall_failure_keeps_system_configuration_record(monkeypatch, stage):
    from core import progress
    from core.sysconfig import SysConfigManager
    from software.base import UninstallError
    from xui import server
    events = []
    monkeypatch.setattr(server, "systemd_available", lambda: True)
    monkeypatch.setattr(progress, "MultiStepProgress", lambda *args: nullcontext(SimpleNamespace(step=lambda *args: None, complete=lambda: events.append("complete"))))
    monkeypatch.setattr(server, "print_action_title", lambda *args: None)
    monkeypatch.setattr(server, "print_success", lambda *args: events.append("success"))
    monkeypatch.setattr(SysConfigManager, "restore", lambda *args: events.append("restore"))
    monkeypatch.setattr(SysConfigManager, "remove", lambda *args: events.append("remove"))

    def fail(name):
        def call(*args, **kwargs):
            events.append(name)
            if name == stage:
                raise OSError("synthetic uninstall failure")
        return call

    monkeypatch.setattr(server, "stop_and_disable_service", fail("service"))
    monkeypatch.setattr(server, "uninstall_traffic_timer", fail("timer"))
    monkeypatch.setattr(server, "remove_xui_artifacts", fail("files"))

    def root(command, **kwargs):
        events.append("sysctl")
        return subprocess.run([sys.executable, "-c", f"raise SystemExit({7 if stage == 'sysctl' else 0})"], **kwargs)

    monkeypatch.setattr(server, "run_as_root", root)
    if stage is None:
        server.uninstall_server()
        assert events == ["service", "timer", "files", "sysctl", "restore", "remove", "complete", "success"]
    else:
        with pytest.raises(UninstallError):
            server.uninstall_server()
        assert not {"restore", "remove", "complete", "success"}.intersection(events)
        assert events[-1] == stage


def test_uninstall_without_service_manager_retains_all_files(monkeypatch):
    from software.base import UninstallError
    from xui import server
    monkeypatch.setattr(server, "systemd_available", lambda: False)

    def forbidden(*args, **kwargs):
        pytest.fail("unconfirmed service manager must not reach uninstall side effects")

    for name in ("stop_and_disable_service", "uninstall_traffic_timer", "remove_xui_artifacts", "run_as_root", "print_success"):
        monkeypatch.setattr(server, name, forbidden)
    with pytest.raises(UninstallError, match="requires systemd"):
        server.uninstall_server()
