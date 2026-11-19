import base64
import hashlib
import socket
import time

import pytest

from core import plugin_upload as upload
from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority


def test_upload_limit_fits_reserved_and_recoverable_workspace_budget():
    from core.plugin_workspace_budget import WORKSPACE_BYTES, MAX_WORKSPACES, MAX_WORKSPACE_BYTES
    assert upload.MAX_FILE <= WORKSPACE_BYTES
    assert MAX_WORKSPACE_BYTES == MAX_WORKSPACES * WORKSPACE_BYTES


def test_snapshot_checks_full_large_file_digest(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    content = b"synthetic upload\0" * 100000
    source = root / "build.bin"
    source.write_bytes(content)
    info = root.stat()
    size, md5 = upload._snapshot(root, source, (info.st_dev, info.st_ino), tmp_path / "snapshot",
                                 hashlib.sha256(content).hexdigest())
    assert size == len(content)
    assert md5 == base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode()
    assert (tmp_path / "snapshot").read_bytes() == content


def test_snapshot_digest_mismatch_rejects_before_network(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"changed")
    info = tmp_path.stat()
    with pytest.raises(ValueError, match="checksum"):
        upload._snapshot(tmp_path, source, (info.st_dev, info.st_ino), tmp_path / "snapshot", "0" * 64)


@pytest.mark.parametrize("relative", ["../private", "/private", "C:/private", "x:stream", "x\\private"])
def test_invalid_source_never_prompts(tmp_path, relative):
    class Authority:
        def require(self, *a, **k):
            pytest.fail("invalid input prompted")
    with pytest.raises(ValueError):
        upload.upload_file("demo", "a" * 64, str(tmp_path), relative,
                           "https://example.com/file", "0" * 64, {}, authority=Authority(),
                           check_identity=lambda *a: True, private_targets=[],
                           deadline=time.monotonic() + 5, checkpoint=lambda: None)


def test_put_streams_without_redirects_and_preserves_presigned_query(tmp_path, monkeypatch):
    from core import plugin_egress
    content = b"x" * (upload.CHUNK + 17)
    source = tmp_path / "snapshot"
    source.write_bytes(content)
    calls, body, headers = [], [], {}
    class Socket:
        def settimeout(self, value): pass
        def connect(self, address): calls.append(address)
        def close(self): pass
    class Response:
        status = 200
        def read(self, limit): return b""
    class Connection:
        def __init__(self, *a, **k): self.sock = None
        def putrequest(self, method, path, **kwargs): calls.append((method, path))
        def putheader(self, name, value): headers[name] = value
        def endheaders(self): pass
        def send(self, chunk): body.append(chunk)
        def getresponse(self): return Response()
        def close(self): pass
    class Context:
        def wrap_socket(self, raw, server_hostname):
            calls.append(server_hostname)
            return raw
    monkeypatch.setattr(plugin_egress, "allowed_addresses", lambda *a: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(upload.socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(upload.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(upload.http.client, "HTTPSConnection", Connection)
    assert upload._put("https://example.com/object?X-Amz-Signature=secret", {}, source,
                       len(content), "known-md5", []) == 200
    assert b"".join(body) == content and len(body) == 2
    assert headers == {"Content-Length": str(len(content)), "Content-MD5": "known-md5"}
    assert calls == [("93.184.216.34", 443), "example.com",
                     ("PUT", "/object?X-Amz-Signature=secret")]


def test_real_worker_private_destination_refused_and_workspace_cleaned(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "build.bin").write_bytes(b"synthetic artifact")
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    required = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kwargs:
                        required(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs))
    with pytest.raises(RuntimeError, match="upload failed") as error:
        upload.upload_file("demo", "a" * 64, str(root), "build.bin",
                           "https://127.0.0.1/private?SECRET", hashlib.sha256(b"synthetic artifact").hexdigest(),
                           {}, authority=authority, check_identity=lambda *a: True, private_targets=[],
                           deadline=time.monotonic() + 20, checkpoint=lambda: None)
    assert "SECRET" not in str(error.value)
    assert not list((tmp_path / "data/plugin-runtimes/workspaces").glob("opskit-sandbox-*"))


def _silent_worker(connection, *args):
    time.sleep(60)


def test_revocation_reaps_silent_upload_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(upload, "_worker", _silent_worker)
    root = tmp_path / "project"
    root.mkdir()
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    required = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kwargs:
                        required(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs))
    checks = 0
    def checkpoint():
        nonlocal checks
        checks += 1
        if checks >= 6:
            raise PermissionError("revoked")
    started = time.monotonic()
    with pytest.raises(PermissionError, match="revoked"):
        upload.upload_file("demo", "a" * 64, str(root), "build.bin", "https://example.com/file",
                           "0" * 64, {}, authority=authority, check_identity=lambda *a: True,
                           private_targets=[], deadline=started + 20, checkpoint=checkpoint)
    assert time.monotonic() - started < 10


def test_broker_rejects_upload_without_network_permission(monkeypatch):
    from types import SimpleNamespace
    from core import plugin_sandbox as sandbox
    monkeypatch.setattr(sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(sandbox, "_remaining", lambda: 10)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda **k: None)
    monkeypatch.setattr(upload, "upload_file", lambda *a, **k: pytest.fail("ungranted upload"))
    with pytest.raises(sandbox.SandboxError, match="network capability"):
        sandbox._broker(SimpleNamespace(name="demo", permissions=[]), {
            "rpc_version": 1, "type": "call", "method": "host.upload_file",
            "args": ["directory", "artifact", "https://example.com/object", "0" * 64, {}], "kwargs": {}})


@pytest.mark.parametrize("language", ["zh", "en"])
def test_real_tls_upload_checks_received_bytes_and_bilingual_summary(tmp_path, monkeypatch, capsys, language):
    import datetime
    import http.server
    import ipaddress
    import ssl
    import threading
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from core import i18n
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpsKit synthetic upload test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False).sign(key, hashes.SHA256()))
    certificate, private_key = tmp_path / "ca.pem", tmp_path / "key.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    content = b"known synthetic file\0" * 80000
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = received[0][1] if received else b""
            self.send_response(200 if received else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, body, self.headers["Content-MD5"]))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    import socket
    server = http.server.HTTPServer(("127.0.0.1", 443), Handler, bind_and_activate=False)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.allow_reuse_address = False
            server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server.server_bind()
        server.server_activate()
    except OSError:
        server.server_close()
        pytest.skip("synthetic TLS port 443 unavailable; no listener replaced")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "build.bin").write_bytes(content)
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    required = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kwargs:
                        required(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs))
    digest = hashlib.sha256(content).hexdigest()
    try:
        result = upload.upload_file("demo", "a" * 64, str(root), "build.bin",
                                    "https://127.0.0.1/object?synthetic-secret", digest, {},
                                    verify_url="https://127.0.0.1/object?synthetic-read-secret",
                                    authority=authority, check_identity=lambda *a: True,
                                    private_targets=["127.0.0.1:443"],
                                    deadline=time.monotonic() + 30, checkpoint=lambda: None)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        authority.close()
    assert result == {"status": 200, "bytes": len(content), "sha256": digest}
    assert received == [("/object?synthetic-secret", content,
                         base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode())]
    logs = capsys.readouterr().out
    assert i18n.t("plugin_native.upload.completed", bytes=len(content)) in logs
    assert "synthetic-secret" not in logs
