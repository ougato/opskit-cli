"""Disposable authenticated smart-HTTPS repository for frozen CLI acceptance.

Host Git is the synthetic server only; acquisition still uses the frozen broker
and its prepared isolated Git runtime. No external credentials or service changes.
"""
from contextlib import contextmanager
import datetime
import http.server
import ipaddress
import socket
import ssl
import subprocess
import threading
import io
import shlex


@contextmanager
def https_repository(output, repository, git):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from core.private_export import publish_private_file

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpsKit synthetic Git")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                       critical=False).sign(key, hashes.SHA256()))
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = output / "git-ca.pem", output / "git-key.pem"
    cert_path.write_bytes(certificate_pem)
    publish_private_file(key_path, key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    requests, failures = [], []
    mode = {"redirect": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def handle_request(self, post):
            self.connection.settimeout(10)
            expected = "/team/repo.git/git-upload-pack" if post else "/team/repo.git/info/refs?service=git-upload-pack"
            requests.append({"method": self.command, "path": self.path,
                             "authenticated": self.headers.get("Authorization") == "Bearer synthetic-git-token"})
            if mode["redirect"]:
                self.send_response(302)
                self.send_header("Location", "/must-not-follow")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path != expected or self.headers.get("Authorization") != "Bearer synthetic-git-token":
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 65536:
                    raise ValueError("fixture request limit")
                body = self.rfile.read(length)
                command = [str(git), "-c", "uploadpack.allowFilter=false", "upload-pack", "--stateless-rpc"]
                if not post:
                    command.append("--advertise-refs")
                result = subprocess.run([*command, str(repository)], input=body,
                                        capture_output=True, check=True, timeout=20)
                body = result.stdout if post else b"001e# service=git-upload-pack\n0000" + result.stdout
                self.send_response(200)
                self.send_header("Content-Type", "application/x-git-upload-pack-" + ("result" if post else "advertisement"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                failures.append(type(error).__name__)
                self.send_error(500)

        def do_GET(self):
            self.handle_request(False)

        def do_POST(self):
            self.handle_request(True)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler, bind_and_activate=False)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.allow_reuse_address = False
            server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server.server_bind()
        server.server_activate()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except BaseException:
        server.server_close()
        raise
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"https://127.0.0.1:{server.server_port}/team/repo.git"
    profile = {"schema_version": 1, "source": url, "authentication": {
        "kind": "bearer", "token": "synthetic-git-token", "ca_pem": certificate_pem.decode()}}
    try:
        yield url, profile, requests, mode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive() or failures:
            raise RuntimeError("synthetic Git server failed: " + repr(failures))


@contextmanager
def ssh_repository(output, repository, git):
    """Loopback SSH with pinned host key and an encrypted synthetic client key."""
    import paramiko
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    host = paramiko.RSAKey.generate(2048)
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                           serialization.BestAvailableEncryption(b"synthetic-passphrase")).decode()
    public = paramiko.Ed25519Key.from_private_key(io.StringIO(pem), password="synthetic-passphrase")
    listener = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(.2)
    stopped = threading.Event()
    requests, failures, handlers = [], [], []

    class Server(paramiko.ServerInterface):
        def __init__(self):
            self.ready = threading.Event()

        def get_allowed_auths(self, username):
            return "publickey"

        def check_auth_publickey(self, username, offered):
            requests.append({"authentication": username})
            return paramiko.AUTH_SUCCESSFUL if username == "git" and offered == public else paramiko.AUTH_FAILED

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            requests.append({"command": command.decode()})
            if shlex.split(command.decode()) != ["git-upload-pack", "/team/repo.git"]:
                return False
            self.ready.set()
            return True

    def serve(raw):
        transport = paramiko.Transport(raw)
        process = channel = reader = None
        try:
            transport.add_server_key(host)
            server = Server()
            transport.start_server(server=server)
            channel = transport.accept(10)
            if channel is None or not server.ready.wait(10):
                return
            process = subprocess.Popen([str(git), "upload-pack", str(repository)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            def input_stream():
                try:
                    while body := channel.recv(65536):
                        process.stdin.write(body)
                        process.stdin.flush()
                except (OSError, ValueError):
                    pass
                finally:
                    process.stdin.close()

            reader = threading.Thread(target=input_stream)
            reader.start()
            while body := process.stdout.read1(65536):
                channel.sendall(body)
            process.stdout.close()
            channel.send_exit_status(process.wait(timeout=10))
            channel.shutdown_write()
        except (EOFError, paramiko.SSHException, ConnectionResetError):
            pass  # Expected when the client refuses a host or key.
        except Exception as error:
            failures.append(type(error).__name__)
        finally:
            if channel is not None:
                channel.close()
            transport.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if reader is not None:
                reader.join(timeout=5)
                if reader.is_alive():
                    failures.append("input thread survived")

    def accept():
        while not stopped.is_set():
            try:
                raw, _ = listener.accept()
            except socket.timeout:
                continue
            handler = threading.Thread(target=serve, args=(raw,))
            handlers.append(handler)
            handler.start()

    thread = threading.Thread(target=accept)
    thread.start()
    url = f"ssh://git@127.0.0.1:{listener.getsockname()[1]}/team/repo.git"
    profile = {"schema_version": 1, "source": url, "authentication": {
        "kind": "ssh_key", "private_key": pem, "passphrase": "synthetic-passphrase",
        "host_keys": [host.get_name() + " " + host.get_base64()]}}
    try:
        yield url, profile, requests, {}
    finally:
        stopped.set()
        thread.join(timeout=5)
        listener.close()
        for handler in handlers:
            handler.join(timeout=15)
        if thread.is_alive() or any(handler.is_alive() for handler in handlers) or failures:
            raise RuntimeError("synthetic SSH server failed: " + repr(failures))
