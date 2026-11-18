"""Real read-only DB/API checks, invalid state and safe report output."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sqlite3
import threading

import pytest

from xui import diagnostics, utils, constants


def test_failed_version_command_is_not_a_healthy_diagnostic_version(monkeypatch):
    from types import SimpleNamespace
    from core import process
    monkeypatch.setattr(utils, "is_service_active", lambda **kwargs: True)
    monkeypatch.setattr(process, "capture", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert utils.detect_xui_version(strict=True) is None
    # Existing installation detection keeps its compatibility fallback.
    assert utils.detect_xui_version() == constants.XUI_INSTALLED_VERSION


def test_real_diagnostic_state_is_bounded_read_only_and_contains_no_secrets(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    value = {"panel_port": 12345, "panel_base_path": "/panel", "panel_password": "private-password",
             "vless": {"port": 443, "private_key": "private-key", "link": "private-link"}}
    path.write_text(json.dumps(value), encoding="utf-8")
    before = path.read_bytes(), path.stat().st_mtime_ns
    monkeypatch.setattr(constants, "XUI_STATE_FILE", path)
    result = diagnostics.read_state(timeout=5)
    assert result == {"panel_port": 12345, "panel_base_path": "/panel", "vless": {"port": 443}}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    path.write_bytes(b" " * 65537)
    assert diagnostics.read_state(timeout=5) == {}


def test_missing_diagnostic_state_does_not_create_file(tmp_path, monkeypatch):
    path = tmp_path / "missing.json"
    monkeypatch.setattr(constants, "XUI_STATE_FILE", path)
    assert diagnostics.read_state(timeout=5) == {}
    assert not path.exists()


def test_report_rejects_late_version_and_does_not_start_next_stage(monkeypatch):
    from core import deadline
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(diagnostics, "read_state", lambda **kwargs: {})
    def version(**kwargs):
        assert kwargs["timeout"] == 2
        clock[0] += 3
        return "1.2.3"
    monkeypatch.setattr(utils, "detect_xui_version", version)
    monkeypatch.setattr(utils, "systemd_available", lambda: pytest.fail("expired budget must stop stages"))
    with pytest.raises(TimeoutError, match="XUI diagnostics"):
        diagnostics.report(timeout=2)


def test_report_passes_remaining_budget_to_service(monkeypatch):
    from core import deadline
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(diagnostics, "read_state", lambda **kwargs: {})
    def version(**kwargs):
        clock[0] += 2
        return "1.2.3"
    def service(**kwargs):
        assert kwargs["timeout"] == 3
        return True
    monkeypatch.setattr(utils, "detect_xui_version", version)
    monkeypatch.setattr(utils, "systemd_available", lambda: True)
    monkeypatch.setattr(utils, "is_service_active", service)
    assert diagnostics.report(timeout=5)["checks"][-1] == {"name": "state_config", "ok": False}


def _database(path, enabled=1, security="reality"):
    with sqlite3.connect(path) as database:
        database.execute("create table inbounds (port integer, protocol text, enable integer, stream_settings text)")
        database.execute("insert into inbounds values (443, 'vless', ?, ?)",
                         (enabled, json.dumps({"security": security, "privateKey": "synthetic-secret"})))


@pytest.mark.parametrize("enabled,security,healthy", [(1, "reality", True), (0, "reality", False), (1, "none", False)])
def test_database_configuration_read_only(tmp_path, enabled, security, healthy):
    path = tmp_path / "panel.db"
    _database(path, enabled, security)
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert diagnostics._inbound_configured(path, 443) is healthy
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    assert not list(tmp_path.glob("*journal*"))


def test_missing_database_not_created(tmp_path):
    path = tmp_path / "missing.db"
    assert not diagnostics._inbound_configured(path, 443)
    assert not path.exists()


def test_database_locked_does_not_wait_beyond_budget(tmp_path):
    import time
    path = tmp_path / "locked.db"
    _database(path)
    with sqlite3.connect(path) as writer:
        writer.execute("begin exclusive")
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="XUI database"):
            diagnostics._inbound_configured(path, 443, timeout=.05)
        assert time.monotonic() - start < 3
        writer.rollback()


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason="POSIX FIFO required")
def test_database_open_on_fifo_is_supervised_and_reaped(tmp_path):
    import multiprocessing
    path = tmp_path / "panel.db"
    os.mkfifo(path, 0o600)
    before = path.stat()
    children = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(TimeoutError, match="XUI database"):
        diagnostics._inbound_configured(path, 443, timeout=.2)
    after = path.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (before.st_dev, before.st_ino, before.st_mode)
    assert {child.pid for child in multiprocessing.active_children()} <= children
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_state_never_probes_api_or_reads_panel_settings(monkeypatch):
    monkeypatch.setattr(diagnostics, "read_state", lambda **kwargs: {"panel_port": True, "panel_base_path": "//external.invalid", "vless": {"port": 443}})
    monkeypatch.setattr(utils, "detect_xui_version", lambda **kwargs: "1")
    monkeypatch.setattr(utils, "systemd_available", lambda: True)
    monkeypatch.setattr(utils, "is_service_active", lambda **kwargs: True)
    monkeypatch.setattr(utils, "get_panel_settings", lambda **kwargs: pytest.fail("invalid state must not drive command parameters"))
    monkeypatch.setattr(diagnostics, "api_available", lambda *args, **kwargs: pytest.fail("invalid state must not contact API"))
    report = diagnostics.report()
    assert not next(check for check in report["checks"] if check["name"] == "state_config")["ok"]


def test_complete_report_with_changed_panel_configuration_fails_without_secrets(tmp_path, monkeypatch):
    path = tmp_path / "panel.db"
    _database(path)
    monkeypatch.setattr(constants, "XUI_DATABASE_FILE", path)
    monkeypatch.setattr(diagnostics, "read_state", lambda **kwargs: {"panel_port": 54321, "panel_base_path": "/opskit",
        "panel_password": "synthetic-secret", "vless": {"port": 443, "private_key": "synthetic-secret"}})
    monkeypatch.setattr(utils, "detect_xui_version", lambda **kwargs: "1")
    monkeypatch.setattr(utils, "systemd_available", lambda: True)
    monkeypatch.setattr(utils, "is_service_active", lambda **kwargs: True)
    monkeypatch.setattr(utils, "get_panel_settings", lambda **kwargs: {"port": 54322, "base_path": "/opskit"})
    monkeypatch.setattr("core.diagnostics.listener_checks", lambda args, **kwargs: [{"name": "listener", "ok": True}])
    monkeypatch.setattr(diagnostics, "api_available", lambda *args, **kwargs: True)
    report = diagnostics.report()
    assert not next(check for check in report["checks"] if check["name"] == "panel_config")["ok"]
    assert "synthetic-secret" not in json.dumps(report)


@pytest.mark.parametrize("reply,healthy", [({"success": True, "obj": "synthetic-token"}, True),
                                           ({"success": False, "obj": ""}, False),
                                           ({"success": True, "obj": "x" * 65537}, False)])
def test_real_loopback_api_protocol(reply, healthy):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == "/opskit/csrf-token"
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert diagnostics.api_available(server.server_port, "/opskit", timeout=5) is healthy
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_api_redirect_is_not_followed():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/forbidden")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert not diagnostics.api_available(server.server_port, "/opskit", timeout=5)
        assert requests == ["/opskit/csrf-token"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
