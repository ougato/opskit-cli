"""Consent-bound artifact metadata without transferring host file contents."""
import hashlib
import json
import re
import time

from core.host_file_access import open_bounded, source_path
from core.host_file_access import MAX_ARTIFACT_BYTES as MAX_FILE
from core.native_sandbox import CapabilityRequest
from core.sdk import HOST_ARTIFACT_BATCH_LIMIT

MAX_BATCH = HOST_ARTIFACT_BATCH_LIMIT


def _worker(connection, root, sources, identity):
    from network.worker import send
    try:
        before = root.lstat()
        if (before.st_dev, before.st_ino) != identity:
            raise ValueError("artifact root changed")
        results = []
        for source in sources:
            digest = hashlib.sha256()
            size = 0
            with open_bounded(source, root, MAX_FILE) as stream:
                while chunk := stream.read(1024**2):
                    size += len(chunk)
                    if size > MAX_FILE:
                        raise ValueError("artifact exceeds limit")
                    digest.update(chunk)
            results.append({"bytes": size, "sha256": digest.hexdigest()})
        after = root.lstat()
        if (after.st_dev, after.st_ino) != identity:
            raise ValueError("artifact root changed")
        send(connection, {"value": results})
    except Exception:
        send(connection, {"error": True})
    finally:
        connection.close()


def artifact_info(plugin, fingerprint, directory, relative, *, authority,
                  check_identity, deadline, checkpoint):
    return artifact_batch_info(plugin, fingerprint, directory, [relative], authority=authority,
                               check_identity=check_identity, deadline=deadline, checkpoint=checkpoint)[0]


def artifact_batch_info(plugin, fingerprint, directory, relatives, *, authority,
                        check_identity, deadline, checkpoint):
    if (not isinstance(relatives, list) or not 1 <= len(relatives) <= MAX_BATCH
            or any(not isinstance(name, str) for name in relatives)
            or len(set(relatives)) != len(relatives)):
        raise ValueError("invalid artifact batch")
    root, source, identity = source_path(directory, relatives[0])
    sources = [source]
    for relative in relatives[1:]:
        path_root, source, current = source_path(directory, relative)
        if path_root != root or current != identity:
            raise ValueError("artifact directory changed")
        sources.append(source)
    scope = CapabilityRequest(plugin, fingerprint, "directory.read", str(root))
    from core.prompt import guard_input
    checkpoint()
    with guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    from core.native_task_control import task_checkpoint
    checkpoint = task_checkpoint(checkpoint)

    def live():
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        current = root.lstat()
        if ((current.st_dev, current.st_ino) != identity
                or getattr(current, "st_file_attributes", 0) & 0x400 or root.is_symlink()):
            raise ValueError("artifact directory changed")
        if time.monotonic() >= (deadline() if callable(deadline) else deadline):
            raise TimeoutError("artifact inspection deadline exceeded")

    from network.worker import owned_process
    live()
    with owned_process(_worker, (root, sources, identity), label="artifact inspection") as (receive, process):
        while True:
            live()
            if receive.poll(0.05):
                message = json.loads(receive.recv_bytes(MAX_BATCH * 128))
                if not isinstance(message, dict) or set(message) != {"value"}:
                    raise RuntimeError("artifact inspection failed")
                results = message["value"]
                if (not isinstance(results, list) or len(results) != len(sources) or any(
                        not isinstance(result, dict) or set(result) != {"bytes", "sha256"}
                        or type(result["bytes"]) is not int or not 0 <= result["bytes"] <= MAX_FILE
                        or not isinstance(result["sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", result["sha256"]) is None for result in results)):
                    raise ValueError("invalid artifact metadata")
                break
            if not process.is_alive():
                raise RuntimeError("artifact inspection worker exited")
    live()
    return results
