"""Exact destination, TLS, framing, cancellation and secret-safe failures."""
import socket
import time

import pytest

from core import native_git_https as https
from core.plugin_fetch_source import remote_source


@pytest.fixture
def transport(monkeypatch):
    from core import plugin_egress
    calls = []
    state = {"status": 200, "Content-Type": "application/x-git-upload-pack-advertisement",
             "Content-Length": "12"}
    class Socket:
        def settimeout(self, value):
            pass
        def connect(self, address):
            calls.append(("connect", address))
        def close(self):
            calls.append("raw-close")
    class Response:
        @property
        def status(self):
            return state["status"]
        def getheader(self, name, default=None):
            return state.get(name, default)
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
        def getresponse(self):
            return Response()
        def close(self):
            calls.append("close")
    class Context:
        def wrap_socket(self, raw, server_hostname):
            calls.append(("sni", server_hostname))
            return raw
    def addresses(host, port, private, *, target):
        calls.append(("resolve", host, port, private, target))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
    monkeypatch.setattr(plugin_egress, "allowed_addresses", addresses)
    monkeypatch.setattr(https.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(https.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(https.http.client, "HTTPSConnection", Connection)
    return calls, state


def test_exact_numeric_destination_sni_path_and_explicit_auth(transport):
    calls, _ = transport
    source = remote_source("https://example.com:8443/team/repository")
    with https._request(source, {"authorization": "Bearer synthetic"}, True, "GET", "/info/refs?service=git-upload-pack"):
        pass
    assert calls[:3] == [("resolve", "example.com", 8443, ["example.com:8443"], ("example.com", 8443)),
                        ("connect", ("93.184.216.34", 8443)), ("sni", "example.com")]
    method, path, options = calls[3]
    assert (method, path) == ("GET", "/team/repository/info/refs?service=git-upload-pack")
    assert options["headers"]["Authorization"] == "Bearer synthetic"
    assert options["headers"]["Accept-Encoding"] == "identity"
    assert calls[-1] == "close"


@pytest.mark.parametrize("change", [{"status": 301}, {"status": 401}, {"Content-Type": "text/html"},
    {"Content-Encoding": "gzip"}, {"Content-Length": "999999999"}, {"Content-Length": "-1"},
    {"Content-Length": "invalid"}])
def test_response_rejected_without_redirect_or_diagnostics(transport, change):
    calls, state = transport
    state.update(change)
    with pytest.raises(ValueError, match="response rejected") as error:
        with https._request(remote_source("https://example.com/repo"),
                            {"authorization": "Bearer SECRET"}, False, "GET", "/info/refs?service=git-upload-pack"):
            pytest.fail("unsafe response accepted")
    assert "SECRET" not in str(error.value)
    assert len([item for item in calls if isinstance(item, tuple) and item[0] == "connect"]) == 1
    assert calls[-1] == "close"


@pytest.mark.parametrize("kwargs", [{"allow_private": 1}, {"branch": "-main"}, {"branch": "main\ncommand"},
    {"branch": 10}, {"deadline": float("nan")}, {"deadline": float("inf")}, {"checkpoint": None}])
def test_invalid_request_rejected_before_spawn(tmp_path, monkeypatch, kwargs):
    from network import worker
    monkeypatch.setattr(worker, "owned_process", lambda *args, **kw: pytest.fail("invalid request spawned"))
    options = {"staging_parent": tmp_path, "deadline": time.monotonic() + 5, "checkpoint": lambda: None}
    options.update(kwargs)
    with pytest.raises(ValueError):
        with https.remote_pack("https://example.com/repo", **options):
            pytest.fail("invalid request accepted")


def _silent_worker(connection, *args):
    time.sleep(60)


def test_cancel_silent_transport_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(https, "_worker", _silent_worker)
    checks = 0
    def checkpoint():
        nonlocal checks
        checks += 1
        if checks >= 4:
            raise PermissionError("cancelled")
    started = time.monotonic()
    with pytest.raises(PermissionError, match="cancelled"):
        with https.remote_pack("https://example.com/repo", staging_parent=tmp_path,
                               deadline=started + 10, checkpoint=checkpoint):
            pytest.fail("silent worker produced a candidate")
    assert time.monotonic() - started < 8
    assert not list(tmp_path.glob("git-pack-*"))


@pytest.mark.parametrize("error,code", [(PermissionError("SECRET"), "fetch_private_denied"),
    (https.ssl.SSLCertVerificationError("SECRET"), "fetch_tls_failed"),
    (ValueError("SECRET"), "fetch_protocol_failed"), (OSError("SECRET"), "fetch_transfer_failed")])
def test_worker_exposes_only_fixed_error_categories(monkeypatch, error, code):
    import io
    from network import worker
    from core import private_file
    class Output(io.BytesIO):
        def fileno(self):
            return 1
    sent = []
    class Connection:
        def close(self):
            pass
    def fail(*args):
        raise error
    monkeypatch.setattr(private_file, "verify_private_file", lambda descriptor: None)
    monkeypatch.setattr(https, "_download", fail)
    monkeypatch.setattr(worker, "send", lambda connection, value: sent.append(value))
    https._worker(Connection(), "https://example.com/repo", None, False, Output(), None)
    assert sent == [{"error": code}]
    assert "SECRET" not in str(sent)
