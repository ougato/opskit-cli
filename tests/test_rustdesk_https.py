"""RustDesk HTTPS downloads retain certificate verification and bounded probes."""
import datetime
from http.server import BaseHTTPRequestHandler
import io
import ipaddress
import ssl
import threading
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from software.base import InstallError
from software.recipes.rustdesk import server
from tests.http_server_fixture import LoopbackHTTPServer


@pytest.fixture
def release_server(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic RustDesk release")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                   .not_valid_after(now + datetime.timedelta(hours=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                                  critical=False).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("hbbs", b"synthetic executable never run" * 100)
    payload = content.getvalue()
    bodies = {"/latest": b'{"tag_name":"v1.2.3"}', "/ip": b"8.8.8.8\n",
              "/html": b"<!doctype html><title>not an IP</title>", "/large": b"x" * 1000}

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.reply(False)

        def do_GET(self):
            self.reply(True)

        def reply(self, send_body):
            body = bodies.get(self.path, payload)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"https://127.0.0.1:{httpd.server_port}"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(server.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(server.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(server, "RUSTDESK_RELEASE_DOWNLOAD", url + "/{version}/{asset}")
    monkeypatch.setattr(server, "RUSTDESK_RELEASE_LATEST_DOWNLOAD", url + "/{asset}")
    monkeypatch.setattr(server, "RUSTDESK_GITHUB_API", url + "/latest")
    monkeypatch.setattr(server, "PUBLIC_IP_APIS", [url + suffix for suffix in ("/html", "/large", "/ip")])
    try:
        yield cert_path, payload
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("version", ["1.2.3", "latest"])
def test_https_release_and_metadata_use_configured_ca(release_server, monkeypatch, version):
    certificate, payload = release_server
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    assert server.latest_version() == "1.2.3"
    assert server.download_release(version).read_bytes() == payload
    assert server.detect_public_host() == "8.8.8.8"


def test_untrusted_release_certificate_is_denied(release_server, tmp_path):
    with pytest.raises(InstallError) as failure:
        server.download_release("1.2.3")
    assert "CERTIFICATE_VERIFY_FAILED" in str(failure.value.__cause__)
    assert not list(tmp_path.glob("*.zip*"))
    assert server.latest_version() == "latest"
    assert server.detect_public_host() == "127.0.0.1"
