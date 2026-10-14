"""RustDesk budgets and rejection of poisoned fallback process identifiers."""
from types import SimpleNamespace

import pytest

from core import deadline, diagnostics, privilege, process
from software.recipes.rustdesk import server


def test_report_stages_share_budget(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    budgets = []
    def files(expected, *, timeout):
        budgets.append(timeout)
        clock[0] += 1
        return [{"name": name, "ok": True} for name, _, _ in expected]
    def service(*args, timeout):
        budgets.append(timeout)
        clock[0] += 1
        return True
    def listeners(expected, *, timeout):
        budgets.append(timeout)
        clock[0] += 1
        return [{"name": "listeners", "ok": True}]
    monkeypatch.setattr(diagnostics, "file_checks", files)
    monkeypatch.setattr(server, "is_process_active", service)
    monkeypatch.setattr(diagnostics, "listener_checks", listeners)
    assert all(check["ok"] for check in server.diagnostic_report(timeout=5)["checks"])
    assert budgets == [5, 4, 3, 2]


def test_late_file_check_stops_service_queries(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    def files(expected, *, timeout):
        clock[0] += timeout + 1
        return []
    monkeypatch.setattr(diagnostics, "file_checks", files)
    monkeypatch.setattr(server, "is_process_active", lambda *a, **k: pytest.fail("expired budget entered service"))
    with pytest.raises(TimeoutError, match="RustDesk diagnostics"):
        server.diagnostic_report(timeout=1)


@pytest.mark.parametrize("pid", ["-1", "0", "1;shutdown", "１２３", "1\n2"])
def test_invalid_pid_never_reaches_kill(monkeypatch, tmp_path, pid):
    monkeypatch.setattr(server, "systemd_is_available", lambda: False)
    monkeypatch.setattr(privilege, "read_unix_root_file", lambda *a, **k: pid)
    monkeypatch.setattr(process, "capture", lambda *a, **k: pytest.fail("invalid PID entered command"))
    assert not server.is_process_active("fixed-service", tmp_path / "pid")


def test_nonzero_systemctl_cannot_claim_active(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "systemd_is_available", lambda: True)
    monkeypatch.setattr(process, "capture", lambda *a, **k: SimpleNamespace(returncode=1, stdout="active"))
    assert not server.is_process_active("fixed-service", tmp_path / "pid")


def test_actual_file_metadata_does_not_read_keys_or_accept_directories(tmp_path):
    key = tmp_path / "key"
    key.write_text("synthetic-secret", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.touch()
    before = key.read_bytes(), key.stat().st_mtime_ns
    checks = diagnostics.file_checks([("key", key, True), ("empty", empty, True),
                                      ("directory", tmp_path, False)], timeout=5)
    assert checks == [{"name": "key", "ok": True}, {"name": "empty", "ok": False},
                      {"name": "directory", "ok": False}]
    assert "synthetic-secret" not in str(checks)
    assert (key.read_bytes(), key.stat().st_mtime_ns) == before
