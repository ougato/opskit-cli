"""Supervised HTTPS PUT of an explicitly authorized, checksum-bound host file."""
import base64
import hashlib
import http.client
import json
import os
import random
import re
import socket
import ssl
import time
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Lock
from urllib.parse import unquote

from core.host_file_access import open_bounded
from core.host_file_access import source_path as _source
from core.host_file_access import MAX_ARTIFACT_BYTES as MAX_FILE
from core.native_sandbox import CapabilityRequest
from core.plugin_http import _validate

CHUNK = 1024**2
ATTEMPTS = 3
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
UPLOAD_PHASES = frozenset({"snapshot", "lookup", "put", "readback"})
UPLOAD_REASONS = frozenset({"http", "timeout", "tls", "network", "destination", "content", "conflict"})


class UploadFailure(RuntimeError):
    def __init__(self, phase, reason, status=0, *, relative=None, retry_after=None):
        super().__init__("plugin file upload failed")
        self.phase, self.reason, self.status = phase, reason, status
        self.relative = relative
        self.retry_after = retry_after


def _failure(phase, error):
    if isinstance(error, UploadFailure):
        return error
    if isinstance(error, TimeoutError):
        reason = "timeout"
    elif isinstance(error, ssl.SSLError):
        reason = "tls"
    elif phase != "snapshot" and isinstance(error, PermissionError):
        reason = "destination"
    elif phase != "snapshot" and isinstance(error, (OSError, http.client.HTTPException)):
        reason = "network"
    else:
        reason = "content"
    return UploadFailure(phase, reason)


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


def _connect_upload(hostname, private_targets):
    from core.plugin_egress import allowed_addresses

    addresses = allowed_addresses(hostname, 443, private_targets)
    context = ssl.create_default_context()
    deadline = time.monotonic() + 10
    error = None
    for index, address in enumerate(addresses):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        raw = socket.socket(address[0], address[1], address[2])
        try:
            raw.settimeout(remaining / (len(addresses) - index))
            raw.connect(address[4])
            secured = context.wrap_socket(raw, server_hostname=hostname)
        except OSError as exc:
            raw.close()
            error = exc
        else:
            secured.settimeout(10)
            return secured
    if error is not None:
        raise error
    raise TimeoutError("upload connection timed out")


def _put(url, headers, snapshot, size, md5, private_targets, sent_bytes=lambda count: None):
    target = _validate(url, "PUT", headers, b"")
    connection = http.client.HTTPSConnection(target.hostname, 443, timeout=10)
    try:
        connection.sock = _connect_upload(target.hostname, private_targets)
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
        if not 200 <= response.status < 300:
            raise UploadFailure("put", "http", response.status, retry_after=response.getheader("Retry-After"))
        if len(response.read(32769)) > 32768:
            raise ValueError("upload response exceeds limit")
        return response.status
    finally:
        connection.close()


def _qetag(snapshot, size):
    block_size = 4 * CHUNK
    with snapshot.open("rb") as source:
        if size <= block_size:
            value = b"\x16" + hashlib.sha1(source.read(block_size)).digest()
        else:
            combined = hashlib.sha1()
            while block := source.read(block_size):
                combined.update(hashlib.sha1(block).digest())
            value = b"\x96" + combined.digest()
    return base64.urlsafe_b64encode(value).decode("ascii")


def _verify(url, size, expected, private_targets, *, phase="lookup"):
    """Check the stored object using Kodo metadata or a stream of the original bytes."""
    stat = isinstance(url, dict)
    headers = {"Accept-Encoding": "identity"}
    if stat:
        headers.update(url["headers"])
        url = url["url"]
    target = _validate(url, "GET", {k: v for k, v in headers.items() if k != "Accept-Encoding"}, b"")
    connection = http.client.HTTPSConnection(target.hostname, 443, timeout=10)
    try:
        connection.sock = _connect_upload(target.hostname, private_targets)
        path = (target.path or "/") + ("?" + target.query if target.query else "")
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status == (612 if stat else 404):
            return False
        if response.status != 200:
            raise UploadFailure(phase, "http", response.status, retry_after=response.getheader("Retry-After"))
        if stat:
            body = response.read(16385)
            if len(body) > 16384:
                raise ValueError("Kodo stat response exceeds limit")
            metadata = json.loads(body)
            if (not isinstance(metadata, dict) or type(metadata.get("fsize")) is not int
                    or not isinstance(metadata.get("hash"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{28}", metadata["hash"])):
                raise ValueError("invalid Kodo stat response")
            if metadata["fsize"] != size or metadata["hash"] != expected:
                raise UploadFailure(phase, "conflict")
            return True
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


def _transient(error):
    return error.reason in {"timeout", "network"} or error.status in RETRYABLE


def _backoff(error, attempt):
    delay = random.uniform(0.25, 0.5) * (2 ** attempt)
    retry_after = error.retry_after
    if retry_after:
        try:
            from email.utils import parsedate_to_datetime
            if retry_after.isascii() and retry_after.isdecimal():
                seconds = int(retry_after)
            else:
                seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            seconds = 0
        if seconds > 10:
            raise error
        delay = max(delay, seconds)
    time.sleep(delay)


def _readback(url, size, digest, private_targets, phase):
    for attempt in range(ATTEMPTS):
        try:
            return _verify(url, size, digest, private_targets, phase=phase)
        except (OSError, ValueError, http.client.HTTPException, UploadFailure) as error:
            failure = _failure(phase, error)
            if not _transient(failure) or attempt + 1 == ATTEMPTS:
                raise failure from None
            _backoff(failure, attempt)


def _transfer(workspace, root, job, private_targets, sent_bytes):
    index, source, identity, url, headers, expected, verify_url, declared_size = job
    snapshot = workspace.root / f"{index}.bin"
    try:
        try:
            size, md5 = _snapshot(root, source, identity, snapshot, expected)
            if size != declared_size:
                raise ValueError("upload source size changed")
        except (OSError, ValueError) as error:
            raise _failure("snapshot", error) from None

        verification_hash = _qetag(snapshot, size) if isinstance(verify_url, dict) else expected

        def verify(phase):
            return _readback(verify_url, size, verification_hash, private_targets, phase)

        status = 200
        if not verify_url or not verify("lookup"):
            conditional = any(key.lower() == "if-none-match" and value == "*"
                              for key, value in headers.items())
            for attempt in range(ATTEMPTS):
                try:
                    status = _put(url, headers, snapshot, size, md5, private_targets, sent_bytes)
                except (OSError, ValueError, http.client.HTTPException, UploadFailure) as error:
                    failure = _failure("put", error)
                    transient = _transient(failure)
                    if not verify_url or not (transient or failure.status in {409, 412}):
                        raise failure from None
                    if verify("readback"):
                        break
                    if not conditional or not transient or attempt + 1 == ATTEMPTS:
                        raise failure from None
                    _backoff(failure, attempt)
                    if verify("lookup"):
                        break
                else:
                    if verify_url and not verify("readback"):
                        raise UploadFailure("readback", "http", 404)
                    break
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
                        results = []
                        failure = None
                        for future in completed:
                            index = active.pop(future)
                            try:
                                results.append([index, future.result()])
                            except UploadFailure as error:
                                failure = [index, error.phase, error.reason, error.status]
                        send(connection, {"sent": transferred, "results": results, "failure": failure})
                        if failure is not None:
                            return False
                        for _ in results:
                            job = next(pending, None)
                            if job is not None:
                                submit(job)
                    return True

                boundary = len(jobs) - publish_last
                if run_group(jobs[:boundary]):
                    run_group(jobs[boundary:])
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        send(connection, {"error": True})
    finally:
        connection.close()


def _validate_kodo_stat(spec, destination):
    if (set(spec) != {"kind", "url", "headers"} or spec["kind"] != "kodo-stat"
            or not isinstance(spec["headers"], dict) or set(spec["headers"]) != {"authorization"}):
        raise ValueError("invalid Kodo stat descriptor")
    target = _validate(spec["url"], "GET", spec["headers"], b"")
    bucket = re.fullmatch(r"(.+)\.s3\.(?:cn|us|ap)-[a-z0-9-]+\.qiniucs\.com", destination.hostname)
    if (not bucket or target.netloc != "rs.qiniuapi.com" or target.query
            or not spec["headers"]["authorization"].startswith("Qiniu ")):
        raise ValueError("invalid Kodo stat endpoint")
    entry = base64.urlsafe_b64encode(
        (bucket[1] + ":" + unquote(destination.path.removeprefix("/"), errors="strict")).encode()
    ).decode("ascii")
    if target.path != "/stat/" + entry:
        raise ValueError("upload verification must target the same Kodo object")


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
        if isinstance(verify_url, dict):
            _validate_kodo_stat(verify_url, destination)
        elif verify_url is not None:
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


def upload_files(plugin, fingerprint, directory, files, publish_last=0, breadcrumb=None, *, authority, check_identity,
                 private_targets, deadline, checkpoint, console=None, input_wait=nullcontext, on_verified=None):
    """Run a bounded batch with one directory grant and one supervised worker."""
    if breadcrumb is not None and (
        not isinstance(breadcrumb, list) or len(breadcrumb) > 16
        or any(not isinstance(label, str) or not 1 <= len(label) <= 256
               or not label.isprintable() for label in breadcrumb)
    ):
        raise ValueError("invalid upload breadcrumb")
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
    with upload_progress(console, total, len(jobs), breadcrumb) as progress:
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
                    if not isinstance(message, dict) or set(message) != {"sent", "results", "failure"}:
                        raise RuntimeError("plugin file upload failed")
                    new_sent = message["sent"]
                    if type(new_sent) is not int or not sent <= new_sent <= ATTEMPTS * total:
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
                    if on_verified is not None:
                        on_verified(len(results))
                    if message["failure"] is not None:
                        index, phase, reason, status = message["failure"]
                        if (type(index) is not int or not 0 <= index < len(jobs) or index in results
                                or phase not in UPLOAD_PHASES or reason not in UPLOAD_REASONS
                                or type(status) is not int or (status and not 100 <= status <= 599)):
                            raise ValueError("invalid upload worker failure")
                        raise UploadFailure(phase, reason, status, relative=files[index][0])
        live()
    return [results[index] for index in range(len(jobs))]


def upload_file(plugin, fingerprint, directory, relative, url, sha256, headers, verify_url=None, **kwargs):
    """Compatibility entry point using the same transfer pipeline."""
    return upload_files(plugin, fingerprint, directory,
                        [[relative, url, sha256, headers, verify_url]], **kwargs)[0]
