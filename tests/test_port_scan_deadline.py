"""Socket concurrency, deadline and cancellation plus native TCP behavior."""
import errno
import socket
import threading

import pytest

from network import commands, scanner


@pytest.fixture
def controlled_scan(monkeypatch):
    state = {"now": 0.0, "connections": [], "peak": 0, "cancel": False}

    class Connection:
        closed = False

        def __init__(self, *args):
            state["connections"].append(self)
            state["peak"] = max(state["peak"], sum(not item.closed for item in state["connections"]))

        def setblocking(self, blocking):
            assert not blocking

        def connect_ex(self, endpoint):
            return errno.EINPROGRESS

        def close(self):
            self.closed = True

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def register(self, *args):
            pass

        def unregister(self, *args):
            pass

        def select(self, timeout):
            if state["cancel"]:
                raise KeyboardInterrupt()
            state["now"] += timeout
            return []

    monkeypatch.setattr(scanner.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(scanner.socket, "socket", Connection)
    monkeypatch.setattr(scanner.selectors, "DefaultSelector", Selector)
    return state


def test_total_timeout_closes_all_inflight_sockets(controlled_scan):
    with pytest.raises(TimeoutError, match="total deadline"):
        commands.scan_ports("127.0.0.1", list(range(1, 100)), timeout=10,
                            concurrency=3, total_timeout=1)
    assert controlled_scan["now"] == 1
    assert controlled_scan["peak"] == 3
    assert len(controlled_scan["connections"]) == 3
    assert all(item.closed for item in controlled_scan["connections"])


def test_per_connection_expiry_preserves_finite_concurrency(controlled_scan):
    results = commands.scan_ports("127.0.0.1", [5, 4, 3, 2, 1], timeout=0.1,
                                 concurrency=2, total_timeout=2)
    assert [item.port for item in results] == [1, 2, 3, 4, 5]
    assert all(not item.open for item in results)
    assert controlled_scan["peak"] == 2
    assert all(item.closed for item in controlled_scan["connections"])


def test_cancel_closes_sockets_without_scan_threads(controlled_scan):
    controlled_scan["cancel"] = True
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(KeyboardInterrupt):
        commands.scan_ports("127.0.0.1", [1, 2], concurrency=2)
    assert all(item.closed for item in controlled_scan["connections"])
    assert {thread.ident for thread in threading.enumerate()} == before


def test_dns_consumes_total_budget_before_any_connection(controlled_scan, monkeypatch):
    from network import resolver

    def resolve(host, **kwargs):
        assert kwargs["timeout"] == 1
        controlled_scan["now"] += 1
        return ["127.0.0.1"]

    monkeypatch.setattr(resolver, "resolve", resolve)
    with pytest.raises(TimeoutError):
        commands.scan_ports("example.invalid", [80], total_timeout=1)
    assert not controlled_scan["connections"]


def test_native_ipv6_listener():
    try:
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        listener.bind(("::1", 0))
        listener.listen()
    except OSError:
        if "listener" in locals():
            listener.close()
        pytest.skip("IPv6 loopback unavailable")
    with listener:
        port = listener.getsockname()[1]
        result = commands.scan_ports("::1", [port], total_timeout=3)
    assert result[0].open


def test_native_closed_tcp_port_is_normal_result():
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
        # Bound without listen: TCP rejects connection while port stays reserved.
        result = commands.scan_ports("127.0.0.1", [port], total_timeout=3)
    assert not result[0].open
