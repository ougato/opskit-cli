"""Supervised HTTPS PUT of an explicitly authorized, checksum-bound host file."""
import base64
import hashlib
import http.client
import json
import re
import socket
import ssl
import time

from core.host_file_access import open_bounded
from core.host_file_access import source_path as _source
from core.host_file_access import MAX_ARTIFACT_BYTES as MAX_FILE
from core.native_sandbox import CapabilityRequest
from core.plugin_http import _validate

CHUNK = 1024**2


def _snapshot(root, source, identity, snapshot, expected):
    before = root.lstat()
    if (before.st_dev, before.st_ino) != identity:
        raise ValueError("upload directory changed")
    sha = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    size = 0
    with open_bounded(source, root, MAX_FILE) as incoming, snapshot.open("xb") as outgoing:
        while chunk := incoming.read(CHUNK):
            size += len(chunk)
            if size > MAX_FILE:
                raise ValueError("upload source exceeds limit")
            sha.update(chunk)
            md5.update(chunk)
            outgoing.write(chunk)
    after = root.lstat()
    if (after.st_dev, after.st_ino) != identity or sha.hexdigest() != expected:
        raise ValueError("upload source identity or checksum changed")
    return size, base64.b64encode(md5.digest()).decode("ascii")


def _put(url, headers, snapshot, size, md5, private_targets):
    target = _validate(url, "PUT", headers, b"")
    from core.plugin_egress import allowed_addresses
    address = allowed_addresses(target.hostname, 443, private_targets)[0]
    connection = http.client.HTTPSConnection(target.hostname, 443, timeout=10)
    raw = socket.socket(address[0], address[1], address[2])
    try:
        raw.settimeout(10)
        raw.connect(address[4])
        connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=target.hostname)
        raw = None
        path = (target.path or "/") + ("?" + target.query if target.query else "")
        connection.putrequest("PUT", path, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Length", str(size))
        connection.putheader("Content-MD5", md5)
        connection.endheaders()
        sent = 0
        with snapshot.open("rb") as source:
            while chunk := source.read(CHUNK):
                sent += len(chunk)
                if sent > size:
                    raise ValueError("upload snapshot changed")
                connection.send(chunk)
        if sent != size:
            raise ValueError("upload snapshot changed")
        response = connection.getresponse()
        # Do not follow redirects or surface potentially secret response bodies.
        if len(response.read(32769)) > 32768 or not 200 <= response.status < 300:
            raise RuntimeError("upload destination rejected request")
        return response.status
    finally:
        connection.close()
        if raw is not None:
            raw.close()


def _worker(connection, workspace, root, source, identity, url, headers, expected, private_targets):
    from network.worker import send
    from core.plugin_workspace import validate_binding
    from core.plugin_workspace_recovery import cleanup_lease
    try:
        with cleanup_lease(workspace.root):
            validate_binding(workspace)
            snapshot = workspace.root / "upload.bin"
            size, md5 = _snapshot(root, source, identity, snapshot, expected)
            status = _put(url, headers, snapshot, size, md5, private_targets)
            send(connection, {"value": {"status": status, "bytes": size, "sha256": expected}})
    except Exception:
        send(connection, {"error": True})
    finally:
        connection.close()


def upload_file(plugin, fingerprint, directory, relative, url, sha256, headers, *,
                authority, check_identity, private_targets, deadline, checkpoint):
    """Network permission is checked by the caller; directory access is broker-owned."""
    destination = _validate(url, "PUT", headers, b"")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("upload requires an exact SHA256")
    root, source, identity = _source(directory, relative)
    scope = CapabilityRequest(plugin, fingerprint, "directory.read", str(root))
    checkpoint()
    from core.prompt import guard_input
    with guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    from core.native_task_control import task_checkpoint
    checkpoint = task_checkpoint(checkpoint)

    def live():
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        if time.monotonic() >= deadline:
            raise TimeoutError("upload deadline exceeded")

    from core.plugin_workspace import owned_workspace
    from network.worker import owned_process
    from core.i18n import t
    from core.prompt import console
    live()
    console.print(t("plugin_native.upload.starting", host=destination.hostname), markup=False)
    with owned_workspace(timeout=min(15, deadline - time.monotonic()), cleanup_timeout=15) as workspace:
        with owned_process(_worker, (workspace, root, source, identity, url, headers, sha256,
                                    private_targets), label="plugin file upload") as (receive, process):
            while True:
                live()
                if receive.poll(0.05):
                    message = json.loads(receive.recv_bytes(65536))
                    if not isinstance(message, dict) or set(message) != {"value"}:
                        raise RuntimeError("plugin file upload failed")
                    result = message["value"]
                    if (not isinstance(result, dict) or set(result) != {"status", "bytes", "sha256"}
                            or type(result["status"]) is not int or not 200 <= result["status"] < 300
                            or type(result["bytes"]) is not int or not 0 <= result["bytes"] <= MAX_FILE
                            or result["sha256"] != sha256):
                        raise ValueError("invalid upload worker result")
                    live()
                    break
                if not process.is_alive():
                    raise RuntimeError("plugin upload worker exited")
    live()
    console.print(t("plugin_native.upload.completed", bytes=result["bytes"]), markup=False)
    return result
