"""One bounded status snapshot and a budget shared by service queries."""
import pytest

from core import deadline
from tailscale import server, diagnostics


def test_linux_report_shares_version_status_and_service_budget(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    seen = []
    def stage(value):
        def call(*, timeout):
            seen.append(timeout)
            clock[0] += 1
            return value
        return call
    monkeypatch.setattr(server, "detect_tailscale_version", stage("1.2.3"))
    monkeypatch.setattr(server, "_status_json", stage({"BackendState": server.TAILSCALE_BACKEND_RUNNING}))
    monkeypatch.setattr(server, "is_service_active", stage(True))
    monkeypatch.setattr(server, "_is_darwin", lambda: False)
    assert all(check["ok"] for check in diagnostics.report(timeout=5)["checks"])
    assert seen == [5, 4, 3]


def test_macos_report_reuses_status_snapshot(monkeypatch):
    monkeypatch.setattr(server, "detect_tailscale_version", lambda **kwargs: "1.2.3")
    calls = []
    def status(**kwargs):
        calls.append(kwargs)
        return {"BackendState": server.TAILSCALE_BACKEND_RUNNING, "AuthURL": "secret-url"}
    monkeypatch.setattr(server, "_status_json", status)
    monkeypatch.setattr(server, "_is_darwin", lambda: True)
    monkeypatch.setattr(server, "is_service_active", lambda **kwargs: pytest.fail("duplicate status query"))
    report = diagnostics.report()
    assert len(calls) == 1
    assert all(check["ok"] for check in report["checks"])
    assert "secret-url" not in str(report)


def test_late_logged_in_status_is_not_success(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(server, "detect_tailscale_version", lambda **kwargs: "1.2.3")
    def status(*, timeout):
        clock[0] += timeout + 1
        return {"BackendState": server.TAILSCALE_BACKEND_RUNNING}
    monkeypatch.setattr(server, "_status_json", status)
    monkeypatch.setattr(server, "is_service_active", lambda **kwargs: pytest.fail("expired report must stop"))
    with pytest.raises(TimeoutError, match="Tailscale diagnostics"):
        diagnostics.report(timeout=2)
