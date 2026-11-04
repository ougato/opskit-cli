"""Real local SSH server fixture; production broker never invokes host Git."""
import io
import os
import shlex
import socket
import subprocess
import threading
import time

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from core.native_git_https import remote_pack
from core.native_sandbox import NativeSandboxError
from core.native_fetch import local_candidate
from tests import test_native_fetch as fetch_fixtures

native_tools = fetch_fixtures.tools


@pytest.fixture
def ssh_repository(native_tools, tmp_path):
    _, _, git = native_tools
    source, commit = fetch_fixtures.repository(tmp_path, git)
    host = paramiko.RSAKey.generate(2048)
    private = ed25519.Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                               serialization.BestAvailableEncryption(b"synthetic-passphrase")).decode()
    public = paramiko.Ed25519Key.from_private_key(io.StringIO(pem), password="synthetic-passphrase")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(.1)
    port = listener.getsockname()[1]
    stopped = threading.Event()
    attempts, commands, errors, handlers = [], [], [], []
    class Server(paramiko.ServerInterface):
        def __init__(self):
            self.ready = threading.Event()
        def get_allowed_auths(self, username):
            return "publickey"
        def check_auth_publickey(self, username, key):
            attempts.append(username)
            return paramiko.AUTH_SUCCESSFUL if username == "git" and key == public else paramiko.AUTH_FAILED
        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        def check_channel_exec_request(self, channel, command):
            commands.append(command)
            if shlex.split(command.decode()) != ["git-upload-pack", "/team/repo.git"]:
                return False
            self.ready.set()
            return True
    def serve(raw):
        transport = paramiko.Transport(raw)
        process = channel = None
        reader = None
        try:
            transport.add_server_key(host)
            server = Server()
            transport.start_server(server=server)
            channel = transport.accept(10)
            if channel is None or not server.ready.wait(10):
                return
            process = subprocess.Popen([git, "upload-pack", str(source)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
            pass  # Expected when client rejects a host or authentication.
        except Exception as error:
            errors.append(repr(error))
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
                    errors.append("fixture input thread survived")
    def accept():
        while not stopped.is_set():
            try:
                raw, _ = listener.accept()
            except socket.timeout:
                continue
            thread = threading.Thread(target=serve, args=(raw,))
            handlers.append(thread)
            thread.start()
    thread = threading.Thread(target=accept)
    thread.start()
    url = f"ssh://git@127.0.0.1:{port}/team/repo.git"
    profile = {"schema_version": 1, "source": url, "authentication": {
        "kind": "ssh_key", "private_key": pem, "passphrase": "synthetic-passphrase",
        "host_keys": [host.get_name() + " " + host.get_base64()]}}
    try:
        yield url, profile, commit, attempts, commands, source
    finally:
        stopped.set()
        thread.join(timeout=5)
        listener.close()
        for handler in handlers:
            handler.join(timeout=15)
            assert not handler.is_alive()
        assert not thread.is_alive()
        assert not errors, errors


@pytest.mark.skipif(os.name != "nt", reason="real AppContainer required")
def test_real_ssh_pinned_host_encrypted_key_and_isolated_export(ssh_repository, native_tools, tmp_path, monkeypatch):
    import copy
    url, profile, commit, attempts, commands, source = ssh_repository
    python, git_runtime, git = native_tools
    def acquire(value, allow_private=True):
        return remote_pack(url, profile=value, allow_private=allow_private, staging_parent=tmp_path,
                           deadline=time.monotonic() + 60, checkpoint=lambda: None)
    with pytest.raises(NativeSandboxError) as denied:
        with acquire(profile, False):
            pytest.fail("private address accepted")
    assert denied.value.code == "fetch_private_denied"
    wrong = copy.deepcopy(profile)
    other = paramiko.RSAKey.generate(2048)
    wrong["authentication"]["host_keys"] = [other.get_name() + " " + other.get_base64()]
    with pytest.raises(NativeSandboxError) as denied:
        with acquire(wrong):
            pytest.fail("untrusted SSH host accepted")
    assert denied.value.code == "fetch_ssh_host_failed"
    assert not attempts and not commands  # No private key sent before host verification.
    wrong = copy.deepcopy(profile)
    wrong["authentication"]["passphrase"] = "incorrect"
    with pytest.raises(NativeSandboxError) as denied:
        with acquire(wrong):
            pytest.fail("wrong passphrase accepted")
    assert denied.value.code == "fetch_ssh_auth_failed"
    with acquire(profile) as (packed, metadata):
        assert metadata["commit"] == commit
        with local_candidate(python, git_runtime, packed, staging_parent=tmp_path,
                             deadline=time.monotonic() + 90, checkpoint=lambda: None,
                             pack=metadata) as (result, _):
            assert result.reported_commit == commit
            assert {item.path for item in result.files} == {"plugin.yaml", "binary.dat"}
    assert attempts and set(attempts) == {"git"}
    assert commands == [b"git-upload-pack /team/repo.git"]
    assert not list(tmp_path.glob("git-pack-*"))
    import json
    import main
    from typer.testing import CliRunner
    from core import cli
    from core.cli_secrets import export_secret
    from core.native_fetch_catalog import FetchCatalog
    from core.plugin import load_manifest
    from core.plugin_trust import trusted_record
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("acquisition must not boot services"))
    FetchCatalog().prepare(python, git_runtime)
    def version(value):
        (source / "plugin.yaml").write_text(
            f"name: native_example\nversion: {value}\napi_version: 3\nkind: python\nentry: native_example\n")
        (source / "native_example.py").write_text("raise RuntimeError('INSTALL_MUST_NOT_IMPORT')\n")
        for arguments in (["add", "plugin.yaml", "native_example.py"], ["commit", "-m", value]):
            subprocess.run([git, "-c", "user.name=Test", "-c", "user.email=native@test.invalid",
                            "-c", "commit.gpgSign=false", "-C", str(source), *arguments],
                           check=True, capture_output=True)
    version("1.0.0")
    profile_file = tmp_path / "private-profile.json"
    export_secret(str(profile_file), profile)
    runner = CliRunner()
    def call(*arguments):
        return runner.invoke(main.app, ["--output", "json", "plugin", *arguments])
    options = ["--fetch-profile", str(profile_file), "--allow-private"]
    installed = call("install", url, "--as", "ssh-example", *options)
    assert installed.exit_code == 0, installed.output
    root = tmp_path / "plugins/ssh-example"
    assert load_manifest(root).version == "1.0.0"
    record = trusted_record("native_example")
    assert record["source"] == url and not record["fingerprint"]
    assert "PRIVATE KEY" not in json.dumps(record) and "synthetic-passphrase" not in json.dumps(record)
    request_count = len(commands)
    assert call("update", "native_example", "--allow-private").exit_code == 1
    assert len(commands) == request_count  # No ambient key/profile reuse.
    version("1.1.0")
    updated = call("update", "native_example", *options)
    assert updated.exit_code == 0, updated.output
    assert load_manifest(root).version == "1.1.0"
    assert not (root / ".git").exists()
    cli.configure("text", False)
