"""Explicit-key SSH transport; only raw Git pack bytes leave this broker."""
import base64
import hmac
import io
import logging
import shlex
import socket

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
    material = authentication(source, profile).payload()
    identity = _identity(paramiko, material)
    expected = []
    algorithms = []
    for line in material["known_hosts"].splitlines():
        _, algorithm, encoded = line.split()
        body = base64.b64decode(encoded, validate=True)
        # Validate actual curve/key data, not just the profile's wire framing.
        paramiko.PKey.from_type_string(algorithm, body)
        expected.append(body)
        algorithms.extend(("rsa-sha2-512", "rsa-sha2-256") if algorithm == "ssh-rsa" else (algorithm,))
    private = [f"{source.host.lower()}:{source.port}"] if allow_private else []
    addresses = allowed_addresses(source.host, source.port, private, target=(source.host, source.port))
    # Never load SSH config, known_hosts, agent, environment passwords or keys.
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
            except paramiko.SSHException:
                raise SSHFailure("fetch_ssh_host_failed") from None
            if not any(hmac.compare_digest(transport.get_remote_server_key().asbytes(), key) for key in expected):
                raise SSHFailure("fetch_ssh_host_failed")
            try:
                transport.auth_publickey(source.user, identity)
                if not transport.is_authenticated():
                    raise SSHFailure("fetch_ssh_auth_failed")
            except paramiko.AuthenticationException:
                raise SSHFailure("fetch_ssh_auth_failed") from None
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
                channel.close()
            if transport is not None:
                transport.close()
