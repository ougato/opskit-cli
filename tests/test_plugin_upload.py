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
    (root / "build.bin").touch()
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
def test_real_tls_upload_checks_received_bytes_and_bilingual_summary(tmp_path, monkeypatch, language):
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
    host = "127.0.0.2"
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpsKit synthetic upload test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]),
                           critical=False).sign(key, hashes.SHA256()))
    certificate, private_key = tmp_path / "ca.pem", tmp_path / "key.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    content = b"known synthetic file\0" * 80000
    received = {}
    parallel_puts = threading.Barrier(2, timeout=5)
    readback = set()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            item = received.get(self.path.split("?")[0])
            body = item[0] if item else b""
            self.send_response(200 if item else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if item:
                readback.add(self.path.split("?")[0])
            self.wfile.write(body)

        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path.startswith("/index.html?"):
                assert readback == {"/build.bin", "/second.bin"}
            else:
                parallel_puts.wait()
            received[self.path.split("?")[0]] = (body, self.headers["Content-MD5"])
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    import socket
    server = http.server.ThreadingHTTPServer((host, 443), Handler, bind_and_activate=False)
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
    (root / "second.bin").write_bytes(content)
    (root / "index.html").write_bytes(content)
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    required = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kwargs:
                        required(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs))
    digest = hashlib.sha256(content).hexdigest()
    try:
        import io
        from contextlib import contextmanager
        from core import plugin_upload_progress
        from rich.console import Console
        from core import prompt
        output = io.StringIO()
        console = Console(file=output, force_terminal=True, width=140)
        monkeypatch.setattr(prompt, "console", console)
        jobs = [
            [name, f"https://{host}/{name}?synthetic-secret", digest, {},
             f"https://{host}/{name}?synthetic-read-secret"]
            for name in ("build.bin", "second.bin", "index.html")]
        verified_files = []
        kwargs = dict(console=console, publish_last=1, on_verified=verified_files.append,
                                    authority=authority, check_identity=lambda *a: True,
                                    private_targets=[f"{host}:443"],
                                    deadline=time.monotonic() + 30, checkpoint=lambda: None)
        title = ["OpsKit", "Cocos", "p0", "1.6.0", "Android"]
        result = upload.upload_files("demo", "a" * 64, str(root), jobs, breadcrumb=title, **kwargs)
        assert verified_files[-1] == 3
        assert all(label in output.getvalue() for label in title)
        updates = []

        @contextmanager
        def capture_progress(console, total, files, breadcrumb=None):
            assert (total, files) == (3 * len(content), 3)
            yield lambda *values: updates.append(values)

        monkeypatch.setattr(plugin_upload_progress, "upload_progress", capture_progress)
        verified_files.clear()
        assert upload.upload_files("demo", "a" * 64, str(root), jobs, **kwargs) == result
        assert verified_files[-1] == 3
        assert updates[-1] == (0, 3 * len(content), 3)
        assert all(sent == 0 for sent, _, _ in updates)
        received["/build.bin"] = (b"corrupt", "")
        updates.clear()
        verified_files.clear()
        with pytest.raises(RuntimeError, match="upload failed"):
            upload.upload_files("demo", "a" * 64, str(root), jobs, **kwargs)
        assert all(verified < 3 * len(content) for _, verified, _ in updates)
        assert all(count < 3 for count in verified_files)
        received["/build.bin"] = (content, received["/second.bin"][1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        authority.close()
    assert result == [{"status": 200, "bytes": len(content), "sha256": digest}] * 3
    expected = (content, base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode())
    assert received == {"/build.bin": expected, "/second.bin": expected, "/index.html": expected}
    logs = output.getvalue()
    assert i18n.t("plugin_native.upload.title") in logs
    assert "100%" in logs and "3/3" in logs and "/s" in logs
    assert "synthetic-secret" not in logs


@pytest.mark.parametrize("matching_remote", [True, False])
def test_conflicting_put_requires_exact_readback(tmp_path, monkeypatch, matching_remote):
    from types import SimpleNamespace

    payload = b"verified object"
    digest = hashlib.sha256(payload).hexdigest()
    root = tmp_path / "source"
    root.mkdir()
    source = root / "file.bin"
    source.write_bytes(payload)
    identity = root.stat().st_dev, root.stat().st_ino
    workspace = SimpleNamespace(root=tmp_path)
    calls = []

    def reject(*args):
        calls.append("put")
        raise upload.UploadFailure("put", "http", 412)

    def verify(*args, phase="lookup"):
        calls.append(phase)
        return phase == "readback" and matching_remote

    monkeypatch.setattr(upload, "_verify", verify)
    monkeypatch.setattr(upload, "_put", reject)
    job = (0, source, identity, "put", {}, digest, "get", len(payload))
    if matching_remote:
        assert upload._transfer(workspace, root, job, [], lambda _: None)["sha256"] == digest
    else:
        with pytest.raises(upload.UploadFailure) as error:
            upload._transfer(workspace, root, job, [], lambda _: None)
        assert (error.value.phase, error.value.reason, error.value.status) == ("put", "http", 412)
    assert calls == ["lookup", "put", "readback"]
    assert not (tmp_path / "0.bin").exists()


def test_retries_missing_object_after_transient_put(monkeypatch, tmp_path):
    from types import SimpleNamespace

    source = tmp_path / "file.bin"
    source.write_bytes(b"x")
    identity = tmp_path.stat().st_dev, tmp_path.stat().st_ino
    digest = hashlib.sha256(b"x").hexdigest()
    calls = []
    stored = False

    def put(*args):
        nonlocal stored
        calls.append("put")
        if calls.count("put") == 1:
            raise upload.UploadFailure("put", "http", 503, retry_after="1")
        stored = True
        return 200

    def verify(*args, phase):
        calls.append(phase)
        return stored

    monkeypatch.setattr(upload, "_put", put)
    monkeypatch.setattr(upload, "_verify", verify)
    monkeypatch.setattr(upload.time, "sleep", lambda delay: calls.append(delay))
    job = (0, source, identity, "put", {"if-none-match": "*"}, digest, "get", 1)
    result = upload._transfer(SimpleNamespace(root=tmp_path), tmp_path, job, [], lambda _: None)
    assert result["sha256"] == digest
    assert calls == ["lookup", "put", "readback", 1, "lookup", "put", "readback"]


@pytest.mark.parametrize("remote_state", ["same", "missing", "conflict"])
def test_kodo_stat_lookup_controls_put(tmp_path, monkeypatch, remote_state):
    from types import SimpleNamespace
    from urllib.parse import quote

    source = tmp_path / "demo.apk"
    content = b"hello"
    source.write_bytes(content)
    key = "prefix/demo.apk"
    entry = base64.urlsafe_b64encode(f"bucket:{key}".encode()).decode()
    descriptor = {"kind": "kodo-stat", "url": f"https://rs.qiniuapi.com/stat/{entry}",
                  "headers": {"authorization": "Qiniu synthetic:signature"}}
    url = "https://bucket.s3.cn-south-1.qiniucs.com/" + quote(key)
    digest = hashlib.sha256(content).hexdigest()
    root, jobs = upload._prepare(str(tmp_path), [[source.name, url, digest, {}, descriptor]])
    calls = []

    def verify(spec, size, checksum, private, *, phase):
        assert spec == descriptor
        assert size == len(content)
        assert checksum == base64.urlsafe_b64encode(b"\x16" + hashlib.sha1(content).digest()).decode()
        calls.append(phase)
        if phase == "lookup" and remote_state == "conflict":
            raise upload.UploadFailure(phase, "conflict")
        return phase == "readback" or remote_state == "same"

    def put(*args):
        calls.append("put")
        return 200

    monkeypatch.setattr(upload, "_verify", verify)
    monkeypatch.setattr(upload, "_put", put)
    workspace = SimpleNamespace(root=tmp_path)
    job = (*jobs[0], len(content))
    if remote_state == "conflict":
        with pytest.raises(upload.UploadFailure) as caught:
            upload._transfer(workspace, root, job, [], lambda n: None)
        assert caught.value.reason == "conflict"
    else:
        assert upload._transfer(workspace, root, job, [], lambda n: None)["sha256"] == digest
    assert calls == (["lookup", "put", "readback"] if remote_state == "missing" else ["lookup"])

    with pytest.raises(ValueError, match="same Kodo object"):
        upload._prepare(str(tmp_path), [[source.name, url + ".different", digest, {}, descriptor]])
