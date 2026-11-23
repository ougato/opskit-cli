"""Verified-host SSH transport; only raw Git pack bytes leave this broker."""
import base64
import hmac
import io
import logging
import shlex
import socket
from pathlib import Path

from core.native_git_protocol import advertisement, read_advertisement, receive_pack
from core.plugin_fetch_source import authentication, remote_source


class SSHFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _identity(paramiko, material):
    for kind in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return kind.from_private_key(io.StringIO(material["private_key"]),
                                         password=material["passphrase"] or None)
        except (ValueError, TypeError, paramiko.SSHException):
            pass
    raise SSHFailure("fetch_ssh_auth_failed")


def download(source, profile, allow_private, destination, branch):
    import paramiko
    from core.plugin_egress import allowed_addresses
    source = remote_source(source)
    if source.kind != "ssh" or type(allow_private) is not bool:
        raise ValueError("invalid Git SSH request")
    agent = None
    if profile is None:
        # Read only exact-host trust entries and standard identities. Never parse
        # SSH config or execute ProxyCommand / credential shell hooks.
        known = paramiko.HostKeys()
        try:
            known.load(str(Path.home() / ".ssh" / "known_hosts"))
            host = source.host if source.port == 22 else f"[{source.host}]:{source.port}"
            keys = known.lookup(host)
        except (OSError, ValueError, paramiko.SSHException):
            keys = None
        if not keys:
            raise SSHFailure("fetch_ssh_host_failed")
        expected = [key.asbytes() for key in keys.values()]
        algorithms = []
        for key in keys.values():
            algorithm = key.get_name()
            algorithms.extend(("rsa-sha2-512", "rsa-sha2-256") if algorithm == "ssh-rsa" else (algorithm,))
        identities = None
    else:
        material = authentication(source, profile).payload()
        identities = [_identity(paramiko, material)]
        expected, algorithms = [], []
        for line in material["known_hosts"].splitlines():
            _, algorithm, encoded = line.split()
            body = base64.b64decode(encoded, validate=True)
            paramiko.PKey.from_type_string(algorithm, body)
            expected.append(body)
            algorithms.extend(("rsa-sha2-512", "rsa-sha2-256") if algorithm == "ssh-rsa" else (algorithm,))
    private = [f"{source.host.lower()}:{source.port}"] if allow_private else []
    addresses = allowed_addresses(source.host, source.port, private, target=(source.host, source.port))
    # Credentials are used only after exact-target server identity verification.
    logger = logging.getLogger("opskit.native.ssh")
    logger.disabled = True
    transport = channel = None
    address = addresses[0]
    with socket.socket(address[0], address[1], address[2]) as raw:
        raw.settimeout(10)
        raw.connect(address[4])
        try:
            transport = paramiko.Transport(raw, disabled_algorithms={
                "kex": ["diffie-hellman-group1-sha1", "diffie-hellman-group14-sha1", "diffie-hellman-group-exchange-sha1"],
                "pubkeys": ["ssh-rsa", "ssh-dss"]})
            transport.set_log_channel("opskit.native.ssh")
            transport.banner_timeout = transport.auth_timeout = 10
            transport.get_security_options().key_types = tuple(dict.fromkeys(algorithms))
            try:
                transport.start_client(timeout=10)
            except paramiko.ssh_exception.IncompatiblePeer:
                raise SSHFailure("fetch_ssh_host_failed") from None
            except (EOFError, OSError, paramiko.SSHException):
                raise SSHFailure("fetch_ssh_connection_failed") from None
            if not any(hmac.compare_digest(transport.get_remote_server_key().asbytes(), key) for key in expected):
                raise SSHFailure("fetch_ssh_host_failed")
            if identities is None:
                identities = []
                try:
                    agent = paramiko.Agent()
                    identities.extend(list(agent.get_keys())[:8])
                except (OSError, paramiko.SSHException):
                    pass
                for filename, kind in (("id_ed25519", paramiko.Ed25519Key),
                                       ("id_ecdsa", paramiko.ECDSAKey), ("id_rsa", paramiko.RSAKey)):
                    try:
                        with (Path.home() / ".ssh" / filename).open("r", encoding="utf-8") as stream:
                            from core.private_file import verify_private_file
                            verify_private_file(stream.fileno())
                            value = stream.read(32769)
                        if len(value) <= 32768:
                            identities.append(kind.from_private_key(io.StringIO(value)))
                    except (OSError, ValueError, paramiko.SSHException):
                        pass
            for identity in identities:
                try:
                    transport.auth_publickey(source.user, identity)
                    if transport.is_authenticated():
                        break
                except paramiko.AuthenticationException:
                    continue
            if not transport.is_authenticated():
                raise SSHFailure("fetch_ssh_auth_failed")
            channel = transport.open_session(timeout=10)
            channel.settimeout(10)
            # Only this fixed remote command; path is POSIX shell quoted.
            # No PTY, forwarding, environment, subsystem or arbitrary commands.
            channel.exec_command("git-upload-pack " + shlex.quote(source.path))
            with channel.makefile("rb") as stream:
                selection, request = advertisement(read_advertisement(stream), branch, http=False)
                channel.sendall(request)
                result = receive_pack(stream, destination, selection)
            if channel.recv_exit_status() != 0:
                raise SSHFailure("fetch_transfer_failed")
            return result
        finally:
            if channel is not None:
                try:
                    channel.close()
                except EOFError:
                    # The remote may close its transport after sending the exit status.
                    pass
            if transport is not None:
                transport.close()
            if agent is not None:
                agent.close()
