"""Supervised Git transport broker; credentials never enter Git or plugins."""
from contextlib import ExitStack, contextmanager
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
                             "fetch_ssh_host_failed", "fetch_ssh_auth_failed", "fetch_http_auth_failed", "fetch_ssh_connection_failed", "fetch_dependency_missing"})


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


def _download(source, profile, allow_private, destination, branch, report=None):
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
        total = response.length
        if response.chunked or total is None or total <= 0:
            return receive_pack(response, destination, selection)
        received = 0
        if report:
            report(total, received)

        class CountedBody:
            def read(self, size):
                nonlocal received
                chunk = response.read(size)
                received += len(chunk)
                if report and chunk:
                    report(total, received)
                return chunk

        result = receive_pack(CountedBody(), destination, selection)
        if received != total:
            raise ValueError("incomplete Git HTTPS response")
        return result


def _worker(connection, source, profile, allow_private, output, branch, show_progress=False):
    from network.worker import send
    try:
        with output:
            from core.private_file import verify_private_file
            verify_private_file(output.fileno())
            output.seek(0)
            output.truncate()
            last_report = 0

            def report(total, received):
                nonlocal last_report
                now = time.monotonic()
                if received in (0, total) or now - last_report >= .1:
                    send(connection, {"progress": [total, received]})
                    last_report = now

            value = _download(source, profile, allow_private, output, branch,
                              report if show_progress else None)
        send(connection, {"value": value})
    except Exception as error:
        from core.native_git_ssh import SSHFailure
        code = ("fetch_dependency_missing" if isinstance(error, ImportError) else
                error.code if isinstance(error, SSHFailure) and error.code in TRANSFER_ERRORS else
                "fetch_private_denied" if isinstance(error, PermissionError) else
                "fetch_tls_failed" if isinstance(error, ssl.SSLError) else
                "fetch_protocol_failed" if isinstance(error, ValueError) else "fetch_transfer_failed")
        send(connection, {"error": code})
    finally:
        connection.close()


@contextmanager
def remote_pack(source, *, profile=None, allow_private=False, branch=None,
                staging_parent, deadline, checkpoint, download_progress=None):
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
        with owned_process(_worker, (source.url, profile, allow_private, _SpawnWritableFile(output.fileno()), branch,
                                    download_progress is not None),
                           label="Git transport") as (receive, process), ExitStack() as progress_scope:
            total = completed = None
            update = None
            while True:
                check()
                if receive.poll(.05):
                    result = json.loads(receive.recv_bytes(65536))
                    if isinstance(result, dict) and set(result) == {"progress"} and download_progress is not None:
                        counts = result["progress"]
                        if (not isinstance(counts, list) or len(counts) != 2
                                or any(type(count) is not int for count in counts)):
                            raise ValueError("invalid Git transfer progress")
                        size, received = counts
                        if not 0 <= received <= size <= MAX_PACK + 65536 or size == 0:
                            raise ValueError("invalid Git transfer progress")
                        if total is None:
                            if received != 0:
                                raise ValueError("invalid initial Git transfer progress")
                            total = size
                            update = progress_scope.enter_context(download_progress(total))
                        elif size != total or received <= completed:
                            raise ValueError("invalid cumulative Git transfer progress")
                        completed = received
                        if update is not None:
                            update(completed)
                        continue
                    if (isinstance(result, dict) and set(result) == {"error"}
                            and isinstance(result["error"], str) and result["error"] in TRANSFER_ERRORS):
                        raise NativeSandboxError(result["error"])
                    if not isinstance(result, dict) or set(result) != {"value"}:
                        raise RuntimeError("remote Git transfer failed")
                    value = result["value"]
                    if total is not None and completed != total:
                        raise ValueError("incomplete Git transfer progress")
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
