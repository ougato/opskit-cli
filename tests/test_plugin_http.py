import time
import pytest

from core import plugin_http as http


@pytest.mark.parametrize("url,method,headers,body", [
    ("http://example.com", "GET", {}, b""),
    ("https://user:pass@example.com", "GET", {}, b""),
    ("https://example.com:8443", "GET", {}, b""),
    ("https://example.com", "CONNECT", {}, b""),
    ("https://example.com", "GET", {"Host": "internal"}, b""),
    ("https://example.com", "GET", {"accept": "a\r\nb"}, b""),
    ("https://example.com", "PUT", {}, b"x" * 32769),
], ids=["scheme", "credentials", "port", "method", "host-header", "newline", "body-limit"])
def test_invalid_requests_fail_before_worker(url, method, headers, body, monkeypatch):
    import network.worker
    monkeypatch.setattr(network.worker, "owned_process", lambda *a, **k: pytest.fail("invalid request spawned"))
    with pytest.raises(ValueError):
        http.request(url, method, headers, body, private_targets=[],
                     deadline=time.monotonic() + 5, checkpoint=lambda: None)


def test_private_destination_refused_by_real_worker():
    with pytest.raises(RuntimeError, match="request failed") as error:
        http.request("https://127.0.0.1/private?SECRET", "GET", {"Authorization": "SECRET"}, b"",
                     private_targets=[], deadline=time.monotonic() + 10, checkpoint=lambda: None)
    assert "SECRET" not in str(error.value)


def test_https_uses_checked_numeric_address_and_preserves_query(monkeypatch):
    import socket
    from core import plugin_egress
    calls = []
    class Socket:
        def settimeout(self, value): pass
        def connect(self, address): calls.append(address)
        def close(self): pass
    class Response:
        status = 302
        def read(self, limit): return b"redirect body"
    class Connection:
        def __init__(self, *a, **k): self.sock = None
        def request(self, method, path, **kwargs): calls.append((method, path))
        def getresponse(self): return Response()
        def close(self): pass
    class Context:
        def wrap_socket(self, raw, server_hostname):
            calls.append(server_hostname)
            return raw
    monkeypatch.setattr(plugin_egress, "allowed_addresses", lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(http.socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(http.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(http.http.client, "HTTPSConnection", Connection)
    result = http._perform("https://example.com/path?q=1", "GET", {}, b"", [])
    assert result["status"] == 302
    assert calls == [("93.184.216.34", 443), "example.com", ("GET", "/path?q=1")]


def test_broker_denies_network_without_manifest_permission(monkeypatch):
    from types import SimpleNamespace
    from core import plugin_sandbox as sandbox
    monkeypatch.setattr(sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(sandbox, "_remaining", lambda: 10)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda **k: None)
    monkeypatch.setattr(http, "request", lambda *a, **k: pytest.fail("ungranted network request"))
    with pytest.raises(sandbox.SandboxError, match="network capability"):
        sandbox._broker(SimpleNamespace(name="demo", permissions=[]), {"rpc_version": 1, "type": "call",
            "method": "host.https", "args": ["https://example.com", "GET", {}, ""], "kwargs": {}})


def _silent_worker(connection, *args):
    time.sleep(60)


def test_silent_https_worker_is_reclaimed_on_revocation(monkeypatch):
    monkeypatch.setattr(http, "_worker", _silent_worker)
    checks = 0
    def checkpoint():
        nonlocal checks
        checks += 1
        if checks >= 4:
            raise PermissionError("revoked")
    started = time.monotonic()
    with pytest.raises(PermissionError, match="revoked"):
        http.request("https://example.com", "GET", {}, b"", private_targets=[],
                     deadline=time.monotonic() + 10, checkpoint=checkpoint)
    assert time.monotonic() - started < 8
