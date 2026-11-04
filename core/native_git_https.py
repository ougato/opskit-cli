"""Supervised Git transport broker; credentials never enter Git or plugins."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import math
from pathlib import Path
import socket
import ssl
import tempfile
import time
from urllib.parse import urlsplit

from core.native_git_protocol import MAX_ADVERTISEMENT, MAX_PACK, advertisement, receive_pack
from core.plugin_fetch_source import authentication, remote_source
from core.private_file import temporary_private_file
from core.native_sandbox import NativeSandboxError


TRANSFER_ERRORS = frozenset({"fetch_private_denied", "fetch_tls_failed", "fetch_protocol_failed", "fetch_transfer_failed",
                             "fetch_ssh_host_failed", "fetch_ssh_auth_failed", "fetch_http_auth_failed", "fetch_ssh_connection_failed"})


@contextmanager
def _request(source, material, allow_private, method, suffix, body=b""):
    from core.plugin_egress import allowed_addresses
    private = [f"{source.host.lower()}:{source.port}"] if allow_private else []
    addresses = allowed_addresses(source.host, source.port, private, target=(source.host, source.port))
    context = ssl.create_default_context()
    if "ca_pem" in material:
        context.load_verify_locations(cadata=material["ca_pem"])
    connection = http.client.HTTPSConnection(source.host, source.port, timeout=10)
    raw = None
    try:
        address = addresses[0]
        raw = socket.socket(address[0], address[1], address[2])
        raw.settimeout(10)
        raw.connect(address[4])
        connection.sock = context.wrap_socket(raw, server_hostname=source.host)
        raw = None
        headers = {"Accept": "application/x-git-" + ("upload-pack-advertisement" if method == "GET" else "upload-pack-result"),
                   "Git-Protocol": "version=0", "Accept-Encoding": "identity"}
        if method == "POST":
            headers["Content-Type"] = "application/x-git-upload-pack-request"
        if "authorization" in material:
            headers["Authorization"] = material["authorization"]
        connection.request(method, urlsplit(source.url).path + suffix, body=body, headers=headers)
        response = connection.getresponse()
        if response.status in (401, 403):
            from core.native_git_ssh import SSHFailure
            raise SSHFailure("fetch_http_auth_failed")
        expected = headers["Accept"]
        length = response.getheader("Content-Length")
        maximum = MAX_ADVERTISEMENT if method == "GET" else MAX_PACK + 65536
        if (response.status != 200 or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != expected
                or response.getheader("Content-Encoding", "identity").lower() != "identity"
                or length is not None and (not length.isdecimal() or int(length) > maximum)):
            raise ValueError("Git HTTPS response rejected")
        # No redirects, proxy discovery, credential helpers, cookies or ambient
        # authorization. Every connection resolves and checks the exact target.
        yield response
    finally:
        connection.close()
        if raw is not None:
            raw.close()


def _download(source, profile, allow_private, destination, branch):
    source = remote_source(source)
    if source.kind == "ssh":
        from core.native_git_ssh import download
        return download(source.url, profile, allow_private, destination, branch)
    if source.kind != "https" or type(allow_private) is not bool:
        raise ValueError("invalid Git HTTPS request")
    material = authentication(source, profile).payload()
    from core.native_git_ssh import SSHFailure
    try:
        with _request(source, material, allow_private, "GET", "/info/refs?service=git-upload-pack") as response:
            raw = response.read(MAX_ADVERTISEMENT + 1)
    except SSHFailure as error:
        if error.code != "fetch_http_auth_failed" or profile is not None:
            raise
        from core.native_git_credentials import https_material
        material = https_material(source)
        if material is None:
            raise
        with _request(source, material, allow_private, "GET", "/info/refs?service=git-upload-pack") as response:
            raw = response.read(MAX_ADVERTISEMENT + 1)
    selection, body = advertisement(raw, branch)
    with _request(source, material, allow_private, "POST", "/git-upload-pack", body) as response:
        return receive_pack(response, destination, selection)


def _worker(connection, source, profile, allow_private, output, branch):
    from network.worker import send
    try:
        with output:
            from core.private_file import verify_private_file
            verify_private_file(output.fileno())
            output.seek(0)
            output.truncate()
            value = _download(source, profile, allow_private, output, branch)
        send(connection, {"value": value})
    except Exception as error:
        from core.native_git_ssh import SSHFailure
        code = (error.code if isinstance(error, SSHFailure) and error.code in TRANSFER_ERRORS else
                "fetch_private_denied" if isinstance(error, PermissionError) else
                "fetch_tls_failed" if isinstance(error, ssl.SSLError) else
                "fetch_protocol_failed" if isinstance(error, ValueError) else "fetch_transfer_failed")
        send(connection, {"error": code})
    finally:
        connection.close()


@contextmanager
def remote_pack(source, *, profile=None, allow_private=False, branch=None,
                staging_parent, deadline, checkpoint):
    """Yield an authenticated transport snapshot, not trusted plugin content.

    Parent owns deadline/cancellation and the exclusively held output descriptor.
    Only the native Git worker is allowed to parse/decompress the downloaded pack.
    """
    source = remote_source(source)
    if profile is not None or source.kind != "ssh":
        authentication(source, profile)
    if (source.kind not in {"https", "ssh"} or type(allow_private) is not bool
            or type(deadline) not in (int, float) or not math.isfinite(deadline)
            or not callable(checkpoint)):
        raise ValueError("invalid Git HTTPS request")
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
                               or branch.startswith("-") or any(ord(c) < 33 for c in branch)):
        raise ValueError("invalid requested Git branch")
    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise TimeoutError("remote Git deadline exceeded")
    check()
    from core.process_file import _SpawnWritableFile
    from network.worker import owned_process
    with temporary_private_file() as output:
        with owned_process(_worker, (source.url, profile, allow_private, _SpawnWritableFile(output.fileno()), branch),
                           label="Git transport") as (receive, process):
            while True:
                check()
                if receive.poll(.05):
                    result = json.loads(receive.recv_bytes(65536))
                    if (isinstance(result, dict) and set(result) == {"error"}
                            and isinstance(result["error"], str) and result["error"] in TRANSFER_ERRORS):
                        raise NativeSandboxError(result["error"])
                    if not isinstance(result, dict) or set(result) != {"value"}:
                        raise RuntimeError("remote Git transfer failed")
                    value = result["value"]
                    break
                if not process.is_alive():
                    raise RuntimeError("remote Git worker exited")
        from core.native_git_protocol import OID
        if (not isinstance(value, dict) or set(value) != {"commit", "shallow", "bytes", "sha256"}
                or not isinstance(value["commit"], str) or not OID.fullmatch(value["commit"].encode())
                or not isinstance(value["shallow"], list) or len(value["shallow"]) > 64
                or any(not isinstance(oid, str) or not OID.fullmatch(oid.encode())
                       or len(oid) != len(value["commit"]) for oid in value["shallow"])
                or type(value["bytes"]) is not int or not 32 <= value["bytes"] <= MAX_PACK
                or not isinstance(value["sha256"], str) or len(value["sha256"]) != 64):
            raise ValueError("invalid remote Git result")
        check()
        with tempfile.TemporaryDirectory(prefix="git-pack-", dir=staging_parent) as temporary:
            root = Path(temporary)
            digest, count = hashlib.sha256(), 0
            output.seek(0)
            with (root / "objects.pack").open("xb") as snapshot:
                while body := output.read(65536):
                    check()
                    count += len(body)
                    if count > value["bytes"]:
                        raise ValueError("remote Git snapshot grew")
                    digest.update(body)
                    snapshot.write(body)
            if count != value["bytes"] or digest.hexdigest() != value["sha256"]:
                raise ValueError("remote Git snapshot changed")
            check()
            yield root, {"commit": value["commit"], "shallow": value["shallow"]}
