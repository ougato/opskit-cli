"""Shared report budgets, supervised queries and no secret config reads."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import deadline, privilege, process
from wireguard import diagnostics, utils, client


def test_server_queries_receive_remaining_budget(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    calls = []
    def active(service, *, timeout):
        calls.append(timeout)
        clock[0] += 1
        return True
    monkeypatch.setattr(utils, "is_service_active", active)
    assert all(check["ok"] for check in diagnostics.server_report(timeout=5)["checks"])
    assert calls == [5, 4, 3]


def test_expired_service_query_never_starts_next_query(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    calls = []
    def active(service, *, timeout):
        calls.append(service)
        clock[0] += timeout + 1
        return True
    monkeypatch.setattr(utils, "is_service_active", active)
    with pytest.raises(TimeoutError, match="WireGuard server diagnostics"):
        diagnostics.server_report(timeout=2)
    assert len(calls) == 1


def test_client_state_and_configs_share_remaining_budget_without_reading_secrets(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    def state(*, timeout):
        assert timeout == 10
        clock[0] += 1
        return {"tunnels": [{"label": "hk", "wg_iface": "wg-hk", "xray_svc": "xray@hk", "token": "secret"}]}
    monkeypatch.setattr(client, "_load_client_state", state)
    budgets = []
    def query(*args, timeout):
        budgets.append(timeout)
        clock[0] += 1
        return True
    monkeypatch.setattr(utils, "is_service_active", query)
    monkeypatch.setattr(privilege, "unix_file_nonempty", query)
    monkeypatch.setattr(privilege, "read_root_file", lambda *a, **k: pytest.fail("config contents must not be read"))
    report = diagnostics.client_report(timeout=10)
    assert budgets == [9, 8, 7, 6]
    assert all(check["ok"] for check in report["checks"])
    assert "secret" not in str(report)


def test_wireguard_service_silent_timeout_is_supervised(monkeypatch):
    original = process.capture
    def silent(command, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
    monkeypatch.setattr(process, "capture", silent)
    with pytest.raises(subprocess.TimeoutExpired):
        utils.is_service_active("fixed-service", timeout=.2)


def test_nonzero_systemctl_cannot_claim_active(monkeypatch):
    monkeypatch.setattr(process, "capture", lambda *a, **k: SimpleNamespace(returncode=1, stdout="active"))
    assert not utils.is_service_active("fixed-service")


def test_bounded_read_stops_before_cat_when_not_regular(monkeypatch):
    calls = []
    def capture(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(privilege, "capture_as_root", capture)
    assert privilege.read_unix_root_file("/fixed/path", timeout=2) is None
    assert calls == [["test", "-f", "/fixed/path"]]


def test_bounded_read_failure_does_not_return_empty_state(monkeypatch):
    calls = []
    def capture(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0 if command[0] == "test" else 1, stdout="")
    monkeypatch.setattr(privilege, "capture_as_root", capture)
    with pytest.raises(OSError, match="diagnostic file read failed"):
        privilege.read_unix_root_file("/fixed/path", timeout=2)
    assert calls[-1] == ["cat", "--", "/fixed/path"]


@pytest.mark.skipif(sys.platform == "win32", reason="real Unix test/cat commands require Unix")
def test_actual_bounded_regular_file_read_and_nonempty(tmp_path, monkeypatch):
    monkeypatch.setattr(privilege, "is_root", lambda: True)
    path = tmp_path / "state.json"
    path.write_text('{"tunnels": []}', encoding="utf-8")
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert privilege.read_unix_root_file(path, timeout=3).strip() == '{"tunnels": []}'
    assert privilege.unix_file_nonempty(path, timeout=3)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    path.write_text("", encoding="utf-8")
    assert not privilege.unix_file_nonempty(path, timeout=3)
