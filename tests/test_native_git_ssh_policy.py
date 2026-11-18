"""SSH command, DNS, key pin and explicit-identity policy, without OS tools."""
import io
import shlex
import socket

import paramiko
import pytest

from core import native_git_ssh as ssh
from core.native_git_protocol import packet
from core.plugin_fetch_source import RemoteSource


@pytest.fixture
def profile():
    key = paramiko.RSAKey.generate(2048)
    pem = io.StringIO()
    key.write_private_key(pem)
    return key, pem.getvalue()


@pytest.mark.parametrize("path", ["/team/repo.git", "/team/repo ' ; touch /tmp/escape", "/team/repo$(command)"])
def test_only_fixed_quoted_command_and_numeric_destination(monkeypatch, profile, path):
    from core import plugin_egress
    key, pem = profile
    source = RemoteSource("ssh", "example.com", 2222, "git", path)
    auth = {"schema_version": 1, "source": source.url, "authentication": {
        "kind": "ssh_key", "private_key": pem, "host_keys": [key.get_name() + " " + key.get_base64()]}}
    calls = []
    oid = b"a" * 40
    pack = b"PACK" + b"untrusted-pack" * 3
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            pass
        def connect(self, address):
            calls.append(("connect", address))
    class Channel:
        def settimeout(self, value):
            pass
        def exec_command(self, command):
            calls.append(("exec", command))
        def makefile(self, mode):
            return io.BytesIO(packet(oid + b" HEAD\0no-progress\n") + b"0000" + packet(b"NAK\n") + pack)
        def sendall(self, body):
            calls.append(("send", body))
        def recv_exit_status(self):
            return 0
        def close(self):
            calls.append("channel-close")
    class Transport:
        def __init__(self, raw, **kwargs):
            calls.append(("policy", kwargs))
        def set_log_channel(self, name):
            pass
        def get_security_options(self):
            return self
        def start_client(self, **kwargs):
            calls.append("handshake")
        def get_remote_server_key(self):
            calls.append("host-verified")
            return key
        def auth_publickey(self, user, identity):
            calls.append(("auth", user, identity.asbytes() == key.asbytes()))
        def is_authenticated(self):
            return True
        def open_session(self, **kwargs):
            return Channel()
        def close(self):
            calls.append("transport-close")
    def addresses(host, port, private, *, target):
        assert (host, port, private, target) == ("example.com", 2222, [], ("example.com", 2222))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 2222))]
    monkeypatch.setattr(plugin_egress, "allowed_addresses", addresses)
    monkeypatch.setattr(ssh.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(paramiko, "Transport", Transport)
    monkeypatch.setenv("SSH_AUTH_SOCK", "SHOULD_NOT_BE_READ")
    monkeypatch.setenv("GIT_SSH_COMMAND", "SHOULD_NOT_RUN")
    output = io.BytesIO()
    result = ssh.download(source.url, auth, False, output, None)
    assert result["commit"] == oid.decode() and output.getvalue() == pack
    command = next(item[1] for item in calls if isinstance(item, tuple) and item[0] == "exec")
    assert shlex.split(command) == ["git-upload-pack", path]
    assert calls.index("host-verified") < calls.index(("auth", "git", True))
    assert calls[0] == ("connect", ("93.184.216.34", 2222))
    assert calls[-2:] == ["channel-close", "transport-close"]
