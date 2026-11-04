"""Verify AppContainer SDK uploads against a private synthetic HTTPS receiver."""
import argparse
import base64
import datetime
import hashlib
import http.server
import ipaddress
import json
import os
from pathlib import Path
import ssl
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def receiver(output, content):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpsKit synthetic receiver")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False).sign(key, hashes.SHA256()))
    certificate, private_key = output / "synthetic-ca.pem", output / "synthetic-key.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    received = []
    expected_md5 = base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            self.connection.settimeout(10)
            if (self.path != "/synthetic-object?token=synthetic"
                    or self.headers.get("Content-Length") != str(len(content))):
                self.send_error(400)
                return
            body = self.rfile.read(len(content))
            valid = body == content and self.headers.get("Content-MD5") == expected_md5
            received.append({"valid": valid, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
            self.send_response(200 if valid else 400)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 443), Handler, bind_and_activate=False)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.allow_reuse_address = False
            server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server.server_bind()
        server.server_activate()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except BaseException:
        server.server_close()
        raise
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, certificate, received


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    args = parser.parse_args()
    output, offline = args.output.resolve(), args.offline.resolve(strict=True)
    output.mkdir(exist_ok=False)
    os.environ["OPSKIT_DATA_DIR"] = str(output / "data")
    from core import i18n
    from core.config import set_config_value
    from core.native_sandbox import GrantChoice
    from core.plugin import load_manifest
    from core.plugin_capabilities import CapabilityAuthority
    from core.plugin_runtime import build
    from core.plugin_sandbox import invoke
    from core.plugin_trust import compute_fingerprint, grant
    i18n.init(language=args.language)
    set_config_value({}, "plugin.sandbox.private_targets.native_upload_probe", ["127.0.0.1:443"])
    project = output / "synthetic-project"
    project.mkdir()
    content = b"OpsKit known synthetic artifact\0" * 80000
    (project / "artifact.bin").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    plugin = output / "plugin"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: native_upload_probe\nversion: 1.0.0\napi_version: 3\nkind: python\n"
        "entry: native_upload_probe\npermissions: [network]\n", encoding="utf-8")
    (plugin / "native_upload_probe.py").write_text(
        "from core.sdk import upload_file, artifact_info, current_lang, plugin_data_dir\n"
        "import json\n"
        "def cli(argv):\n"
        "    (plugin_data_dir('native_upload_probe')/'completed.txt').write_text('must-not-commit')\n"
        "    info=artifact_info(argv[0],'artifact.bin')\n"
        "    if info['sha256']!=argv[2]: raise RuntimeError('artifact checksum mismatch')\n"
        "    result=upload_file(argv[0],'artifact.bin',argv[1],info['sha256'])\n"
        "    (plugin_data_dir('native_upload_probe')/'completed.txt').write_text(current_lang())\n"
        "    (plugin_data_dir('native_upload_probe')/'result.json').write_text(json.dumps(result))\n"
        "    return 0\n", encoding="utf-8")
    build(plugin, [], offline / "python", wheels=offline / "wheels",
          lock_file=offline / "requirements.lock")
    fingerprint = compute_fingerprint(plugin)
    grant("native_upload_probe", fingerprint, "1.0.0")
    required = CapabilityAuthority.require
    scopes = []

    def authorize(self, scope, **kwargs):
        if (scope.plugin != "native_upload_probe" or scope.fingerprint != fingerprint
                or scope.operation != "directory.read" or Path(scope.resource) != project):
            raise RuntimeError("unexpected synthetic upload authorization")
        def prompt(_):
            scopes.append(scope.resource)
            return GrantChoice.ONCE
        return required(self, scope, prompt=prompt, **kwargs)

    server, thread, certificate, received = receiver(output, content)
    previous_ca = os.environ.get("SSL_CERT_FILE")
    os.environ["SSL_CERT_FILE"] = str(certificate)
    CapabilityAuthority.require = authorize
    state = output / "data/plugin-data/native_upload_probe/self/state/completed.txt"
    cancelled = False
    try:
        exit_code = invoke(load_manifest(plugin), "cli", args=[str(project),
                        "https://127.0.0.1/synthetic-object?token=synthetic", digest])
        if state.read_text() != args.language or len(scopes) != 1:
            raise RuntimeError("successful upload state/one-time consent mismatch")
        from core import native_task_control
        original_control = native_task_control.task_checkpoint
        def cancellation(check, **kwargs):
            def stop():
                check()
                raise KeyboardInterrupt
            return stop
        native_task_control.task_checkpoint = cancellation
        try:
            try:
                invoke(load_manifest(plugin), "cli", args=[str(project),
                    "https://127.0.0.1/synthetic-object?token=synthetic", digest])
            except KeyboardInterrupt:
                cancelled = True
        finally:
            native_task_control.task_checkpoint = original_control
    finally:
        CapabilityAuthority.require = required
        if previous_ca is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = previous_ca
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    result = json.loads(state.with_name("result.json").read_text())
    if (exit_code != 0 or result != {"status": 200, "bytes": len(content), "sha256": digest}
            or received != [{"valid": True, "sha256": digest, "bytes": len(content)}]
            or state.read_text() != args.language or len(scopes) != 2 or not cancelled):
        raise RuntimeError("native SDK upload acceptance failed")
    (output / "verification.json").write_text(json.dumps({
        "result": result, "language": args.language, "formal_sdk_verified": True,
        "artifact_info_verified": True,
        "injected_task_cancellation_rollback_verified": True,
        "receiver_verified": received, "one_time_authorization": True,
        "state_committed": True, "frozen_cli_verified": False, "actual_s3_verified": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
