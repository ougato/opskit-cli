"""Listener ownership, visibility failures and real native sockets."""
import socket
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from core.diagnostics import listener_checks


def test_other_process_on_same_port_does_not_pass(monkeypatch, tmp_path):
    connection = SimpleNamespace(type=socket.SOCK_STREAM, laddr=SimpleNamespace(port=21116),
                                 status=psutil.CONN_LISTEN, pid=123)
    monkeypatch.setattr(psutil, "net_connections", lambda **kwargs: [connection])
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(exe=lambda: str(tmp_path / "other")))
    checks = listener_checks([("tcp", 21116, tmp_path / "hbbs")])
    assert not checks[0]["ok"]
    assert checks[0]["reason"] == "listener_owner_not_verified"


def test_socket_inspection_denied_is_not_healthy(monkeypatch):
    def denied(**kwargs):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "net_connections", denied)
    checks = listener_checks([("udp", 21116, Path("hbbs"))])
    assert checks == [{"name": "udp:21116", "ok": False, "reason": "listeners_unavailable"}]


def test_established_tcp_connection_is_not_listener(monkeypatch):
    connection = SimpleNamespace(type=socket.SOCK_STREAM, laddr=SimpleNamespace(port=21116),
                                 status=psutil.CONN_ESTABLISHED, pid=123)
    monkeypatch.setattr(psutil, "net_connections", lambda **kwargs: [connection])
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("non-listener must not query process"))
    assert not listener_checks([("tcp", 21116, Path("hbbs"))])[0]["ok"]


def test_process_ownership_query_is_cached_per_pid(monkeypatch, tmp_path):
    rows = [SimpleNamespace(type=socket.SOCK_STREAM, laddr=SimpleNamespace(port=port),
                            status=psutil.CONN_LISTEN, pid=123) for port in (21115, 21116)]
    calls = []
    monkeypatch.setattr(psutil, "net_connections", lambda **kwargs: rows)

    def process(pid):
        calls.append(pid)
        return SimpleNamespace(exe=lambda: str(tmp_path / "hbbs"))

    monkeypatch.setattr(psutil, "Process", process)
    checks = listener_checks([("tcp", port, tmp_path / "hbbs") for port in (21115, 21116)])
    assert all(item["ok"] for item in checks)
    assert calls == [123]


@pytest.mark.parametrize("protocol", ["tcp", "udp"])
@pytest.mark.parametrize("timeout", [None, 5])
def test_real_native_listener_and_executable_ownership(protocol, timeout):
    # macOS can deny whole-machine socket enumeration to ordinary users.
    # Independently check visibility; denial must remain an unhealthy result.
    try:
        psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        visible = False
    else:
        visible = True
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as listener:
        listener.bind(("127.0.0.1", 0))
        if protocol == "tcp":
            listener.listen()
        port = listener.getsockname()[1]
        # Windows venv launchers can differ from the actual process image.
        checks = listener_checks([(protocol, port, Path(psutil.Process().exe()))], timeout=timeout)
    if visible:
        assert checks[0]["ok"], checks
    else:
        assert checks == [{"name": f"{protocol}:{port}", "ok": False, "reason": "listeners_unavailable"}]


def _slow_listener_worker(connection, expectations):
    import time
    from network.worker import send
    Path(expectations[0][2]).write_text("started", encoding="utf-8")
    send(connection, {"progress": "started"})
    time.sleep(30)


def test_listener_timeout_reaps_worker(monkeypatch, tmp_path):
    import multiprocessing
    import time
    from core import diagnostics
    monkeypatch.setattr(diagnostics, "_listener_worker", _slow_listener_worker)
    before = {child.pid for child in multiprocessing.active_children()}
    start = time.monotonic()
    marker = tmp_path / "worker-started"
    with pytest.raises(TimeoutError, match="listener diagnostics"):
        listener_checks([("tcp", 12345, marker)], timeout=5)
    assert marker.read_text(encoding="utf-8") == "started"
    assert time.monotonic() - start < 8
    assert {child.pid for child in multiprocessing.active_children()} == before
