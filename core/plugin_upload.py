"""Supervised HTTPS PUT of an explicitly authorized, checksum-bound host file."""
import base64
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import time
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Lock

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


def _put(url, headers, snapshot, size, md5, private_targets, sent_bytes=lambda count: None):
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
                sent_bytes(len(chunk))
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


def _verify(url, size, expected, private_targets):
    """Stream and compare the stored bytes without exposing them to the plugin."""
    target = _validate(url, "GET", {}, b"")
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
        connection.request("GET", path, headers={"Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status == 404:
            return False
        if response.status != 200:
            raise ValueError("upload verification request failed")
        digest = hashlib.sha256()
        received = 0
        while chunk := response.read(min(CHUNK, size - received + 1)):
            received += len(chunk)
            if received > size:
                raise ValueError("stored artifact size mismatch")
            digest.update(chunk)
        if received != size or digest.hexdigest() != expected:
            raise ValueError("stored artifact checksum mismatch")
        return True
    finally:
        connection.close()
        if raw is not None:
            raw.close()


def _transfer(workspace, root, job, private_targets, sent_bytes):
    index, source, identity, url, headers, expected, verify_url, declared_size = job
    snapshot = workspace.root / f"{index}.bin"
    try:
        size, md5 = _snapshot(root, source, identity, snapshot, expected)
        if size != declared_size:
            raise ValueError("upload source size changed")
        status = 200
        if not verify_url or not _verify(verify_url, size, expected, private_targets):
            status = _put(url, headers, snapshot, size, md5, private_targets, sent_bytes)
            if verify_url and not _verify(verify_url, size, expected, private_targets):
                raise ValueError("stored artifact missing after upload")
        return {"status": status, "bytes": size, "sha256": expected}
    finally:
        snapshot.unlink(missing_ok=True)


def _worker(connection, workspace, root, jobs, private_targets, publish_last):
    from network.worker import send
    from core.plugin_workspace import validate_binding
    from core.plugin_workspace_recovery import cleanup_lease

    sent = 0
    lock = Lock()

    def sent_bytes(count):
        nonlocal sent
        with lock:
            sent += count

    try:
        with cleanup_lease(workspace.root):
            validate_binding(workspace)
            workers = min(len(jobs), max(4, min(16, 2 * (os.cpu_count() or 1))))
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                active = {}

                def submit(job):
                    active[pool.submit(_transfer, workspace, root, job, private_targets, sent_bytes)] = job[0]

                def run_group(group):
                    pending = iter(group)
                    for _ in range(min(workers, len(group))):
                        submit(next(pending))
                    while active:
                        completed, _ = wait(active, timeout=0.2, return_when=FIRST_COMPLETED)
                        with lock:
                            transferred = sent
                        results = [[active.pop(future), future.result()] for future in completed]
                        send(connection, {"sent": transferred, "results": results})
                        for _ in results:
                            job = next(pending, None)
                            if job is not None:
                                submit(job)

                boundary = len(jobs) - publish_last
                run_group(jobs[:boundary])
                run_group(jobs[boundary:])
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        send(connection, {"error": True})
    finally:
        connection.close()


def _prepare(directory, files):
    if not isinstance(files, list) or not 1 <= len(files) <= 4096:
        raise ValueError("upload batch must contain 1 to 4096 files")
    jobs = []
    sources = set()
    targets = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, list) or len(entry) != 5:
            raise ValueError("invalid upload batch entry")
        relative, url, sha256, headers, verify_url = entry
        destination = _validate(url, "PUT", headers, b"")
        if verify_url is not None:
            verification = _validate(verify_url, "GET", {}, b"")
            if (verification.netloc, verification.path) != (destination.netloc, destination.path):
                raise ValueError("upload verification must target the same object")
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ValueError("upload requires an exact SHA256")
        root, source, identity = _source(directory, relative)
        target = (destination.netloc, destination.path)
        if source in sources or target in targets:
            raise ValueError("duplicate upload source or destination")
        sources.add(source)
        targets.add(target)
        jobs.append((index, source, identity, url, headers, sha256, verify_url))
    return root, jobs


def upload_files(plugin, fingerprint, directory, files, publish_last=0, *, authority, check_identity,
                 private_targets, deadline, checkpoint, console=None, input_wait=nullcontext):
    """Run a bounded batch with one directory grant and one supervised worker."""
    root, jobs = _prepare(directory, files)
    if type(publish_last) is not int or not 0 <= publish_last <= len(jobs):
        raise ValueError("invalid final upload group")
    scope = CapabilityRequest(plugin, fingerprint, "directory.read", str(root))
    checkpoint()
    from core.prompt import guard_input
    with input_wait(), guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    deadline = deadline() if callable(deadline) else deadline
    from core.native_task_control import task_checkpoint
    checkpoint = task_checkpoint(checkpoint)

    def live():
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        if time.monotonic() >= deadline:
            raise TimeoutError("upload deadline exceeded")

    from core.plugin_workspace import owned_workspace
    from network.worker import owned_process
    from core.plugin_upload_progress import upload_progress
    results = {}
    sized_jobs = []
    for job in jobs:
        size = job[1].lstat().st_size
        if not 0 <= size <= MAX_FILE:
            raise ValueError("upload source exceeds limit")
        sized_jobs.append((*job, size))
    jobs = sized_jobs
    total = sum(job[-1] for job in jobs)
    sent = verified = 0
    live()
    with upload_progress(console, total, len(jobs)) as progress:
        with owned_workspace(timeout=min(15, deadline - time.monotonic()), cleanup_timeout=15) as workspace:
            with owned_process(_worker, (workspace, root, jobs, private_targets, publish_last),
                               label="plugin file upload") as (receive, process):
                while len(results) < len(jobs):
                    live()
                    if not receive.poll(0.1):
                        if not process.is_alive():
                            raise RuntimeError("plugin upload worker exited")
                        continue
                    message = json.loads(receive.recv_bytes(65536))
                    if not isinstance(message, dict) or set(message) != {"sent", "results"}:
                        raise RuntimeError("plugin file upload failed")
                    new_sent = message["sent"]
                    if type(new_sent) is not int or not sent <= new_sent <= total:
                        raise ValueError("invalid upload byte count")
                    sent = new_sent
                    for index, result in message["results"]:
                        if type(index) is not int or not 0 <= index < len(jobs) or index in results:
                            raise ValueError("invalid upload result index")
                        if (not isinstance(result, dict) or set(result) != {"status", "bytes", "sha256"}
                                or type(result["status"]) is not int or not 200 <= result["status"] < 300
                                or type(result["bytes"]) is not int or result["bytes"] != jobs[index][-1]
                                or result["sha256"] != jobs[index][-3]):
                            raise ValueError("invalid upload worker result")
                        results[index] = result
                        verified += result["bytes"]
                    progress(sent, verified, len(results))
        live()
    return [results[index] for index in range(len(jobs))]


def upload_file(plugin, fingerprint, directory, relative, url, sha256, headers, verify_url=None, **kwargs):
    """Compatibility entry point using the same transfer pipeline."""
    return upload_files(plugin, fingerprint, directory,
                        [[relative, url, sha256, headers, verify_url]], **kwargs)[0]
