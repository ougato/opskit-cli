#!/usr/local/bin/python -I
"""Fixed-operation SSH client, bounded CONNECT relay and immutable askpass."""
import base64
import json
import os
from pathlib import Path
import select
import shlex
import socket
import sys
import time

HELPER = "/runtime/core/plugin_fetch_ssh.py"


def configure(source, profile, proxy, work):
    from core.plugin_fetch_source import authentication, remote_source
    from core.plugin_fetch_transport import private_write, proxy_binding, quoted
    source = remote_source(source)
    if source.kind != "ssh":
        raise ValueError("SSH source required")
    material = authentication(source, profile).payload()
    address, port, token = proxy_binding(proxy)
    home = Path(work) / "home"
    private_write(home / "identity", material["private_key"])
    private_write(home / "known_hosts", material["known_hosts"])
    private_write(home / "passphrase", material["passphrase"])
    private_write(home / "binding.json", json.dumps({"source": source.url,
                  "proxy": {"address": address, "port": port, "token": token}}, ensure_ascii=False))
    launcher = shlex.join(["/usr/local/bin/python", "-I", "-S", HELPER, "client", str(home / "binding.json")])
    private_write(home / "gitconfig", '[protocol]\n allow = never\n[protocol "ssh"]\n allow = always\n'
                  '[protocol]\n version = 0\n[ssh]\n variant = ssh\n[core]\n sshCommand = ' + quoted(launcher) + '\n')
    return source.url


def _binding(path):
    from core.plugin_fetch_source import remote_source
    from core.plugin_fetch_transport import proxy_binding
    with Path(path).open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("invalid SSH binding")
    record = json.loads(raw)
    if not isinstance(record, dict) or set(record) != {"source", "proxy"}:
        raise ValueError("invalid SSH binding")
    source = remote_source(record["source"])
    if source.kind != "ssh":
        raise ValueError("invalid SSH binding")
    return source, proxy_binding(record["proxy"])


def _client(path):
    source, _ = _binding(path)
    home = Path(path).parent
    proxy = shlex.join(["/usr/local/bin/python", "-I", "-S", HELPER, "connect", str(path)]).replace('%', '%%')
    options = ["UserKnownHostsFile=" + str(home / "known_hosts"), "GlobalKnownHostsFile=/dev/null",
               "StrictHostKeyChecking=yes", "VerifyHostKeyDNS=no", "UpdateHostKeys=no", "CheckHostIP=no",
               "CanonicalizeHostname=no", "IdentityAgent=none", "IdentitiesOnly=yes", "AddKeysToAgent=no",
               "CertificateFile=none", "PreferredAuthentications=publickey", "PubkeyAuthentication=yes",
               "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "GSSAPIAuthentication=no",
               "HostbasedAuthentication=no", "NumberOfPasswordPrompts=1", "PermitLocalCommand=no",
               "ClearAllForwardings=yes", "ForwardAgent=no", "ForwardX11=no", "RequestTTY=no",
               "ConnectionAttempts=1", "ConnectTimeout=10", "ServerAliveInterval=10", "ServerAliveCountMax=1",
               "ControlMaster=no", "ControlPath=none", "LogLevel=ERROR", "ProxyCommand=" + proxy]
    command = ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-e", "none"]
    for option in options:
        command += ["-o", option]
    command += ["-i", str(home / "identity"), "-p", str(source.port), "-l", source.user, "--", source.host,
                "git-upload-pack " + shlex.quote(source.path)]
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                   "SSH_ASKPASS": HELPER, "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": "opskit:0",
                   "OPSKIT_FETCH_ASKPASS_BINDING": str(path)}
    # Git's appended SSH arguments are deliberately not an authority. This
    # launcher always reads the single bound source using git-upload-pack.
    os.execve(command[0], command, environment)


def _askpass(path):
    if len(sys.argv) != 2 or not sys.argv[1].startswith("Enter passphrase for key "):
        raise ValueError("unexpected SSH authentication prompt")
    _binding(path)
    with (Path(path).parent / "passphrase").open("rb") as stream:
        value = stream.read(4097)
    if len(value) > 4096 or b"\n" in value or b"\r" in value:
        raise ValueError("invalid SSH passphrase")
    sys.stdout.buffer.write(value + b"\n")


def _tunnel(path):
    source, (address, port, token) = _binding(path)
    authorization = base64.b64encode(("opskit:" + token).encode()).decode()
    request = (f"CONNECT {source.authority} HTTP/1.1\r\nHost: {source.authority}\r\n"
               f"Proxy-Authorization: Basic {authorization}\r\n\r\n").encode("ascii")
    with socket.create_connection((address, port), timeout=10) as upstream:
        upstream.sendall(request)
        header = bytearray()
        deadline = time.monotonic() + 15
        while not header.endswith(b"\r\n\r\n"):
            upstream.settimeout(max(.001, deadline - time.monotonic()))
            if len(header) >= 16384 or time.monotonic() >= deadline:
                raise ValueError("SSH proxy response budget exceeded")
            byte = upstream.recv(1)
            if not byte:
                raise ValueError("SSH proxy connection refused")
            header.extend(byte)
        status = bytes(header).split(b"\r\n", 1)[0].split(b" ", 2)
        if len(status) < 2 or status[0] not in (b"HTTP/1.0", b"HTTP/1.1") or status[1] != b"200":
            raise ValueError("SSH proxy connection refused")
        _relay(upstream)


def _relay(upstream):
    """At most 64KiB buffered per direction; total lifetime is bounded."""
    upstream.setblocking(False)
    os.set_blocking(0, False)
    os.set_blocking(1, False)
    to_remote, to_local = bytearray(), bytearray()
    input_eof = remote_eof = write_closed = False
    deadline = time.monotonic() + 300
    while not remote_eof or to_local:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SSH tunnel deadline exceeded")
        reads = ([0] if not input_eof and not to_remote and not remote_eof else [])
        if not remote_eof and not to_local:
            reads.append(upstream)
        writes = ([upstream] if to_remote and not remote_eof else []) + ([1] if to_local else [])
        readable, writable, _ = select.select(reads, writes, [], min(remaining, 10))
        if 0 in readable:
            chunk = os.read(0, 65536)
            if chunk:
                to_remote.extend(chunk)
            else:
                input_eof = True
        if upstream in writable:
            del to_remote[:upstream.send(to_remote)]
        if input_eof and not to_remote and not write_closed:
            upstream.shutdown(socket.SHUT_WR)
            write_closed = True
        if upstream in readable:
            chunk = upstream.recv(65536)
            if chunk:
                to_local.extend(chunk)
            else:
                remote_eof = True
        if 1 in writable:
            del to_local[:os.write(1, to_local)]


def main():
    try:
        if len(sys.argv) >= 3 and sys.argv[1] in ("client", "connect"):
            (_client if sys.argv[1] == "client" else _tunnel)(sys.argv[2])
        elif os.environ.get("OPSKIT_FETCH_ASKPASS_BINDING"):
            _askpass(os.environ["OPSKIT_FETCH_ASKPASS_BINDING"])
        else:
            raise ValueError("invalid SSH transport mode")
        return 0
    except (OSError, ValueError, RuntimeError):
        print("isolated SSH transport failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.exit(main())
