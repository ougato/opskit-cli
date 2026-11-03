"""Consent-bound artifact metadata without transferring host file contents."""
import hashlib
import json
import re
import time

from core.host_file_access import open_bounded, source_path
from core.host_file_access import MAX_ARTIFACT_BYTES as MAX_FILE
from core.native_sandbox import CapabilityRequest

def _worker(connection, root, source, identity):
    from network.worker import send
    try:
        before = root.lstat()
        if (before.st_dev, before.st_ino) != identity:
            raise ValueError("artifact root changed")
        digest = hashlib.sha256()
        size = 0
        with open_bounded(source, root, MAX_FILE) as stream:
            while chunk := stream.read(1024**2):
                size += len(chunk)
                if size > MAX_FILE:
                    raise ValueError("artifact exceeds limit")
                digest.update(chunk)
        after = root.lstat()
        if (after.st_dev, after.st_ino) != identity:
            raise ValueError("artifact root changed")
        send(connection, {"value": {"bytes": size, "sha256": digest.hexdigest()}})
    except Exception:
        send(connection, {"error": True})
    finally:
        connection.close()


def artifact_info(plugin, fingerprint, directory, relative, *, authority,
                  check_identity, deadline, checkpoint):
    root, source, identity = source_path(directory, relative)
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
        if time.monotonic() >= deadline:
            raise TimeoutError("artifact inspection deadline exceeded")

    from network.worker import owned_process
    live()
    with owned_process(_worker, (root, source, identity), label="artifact inspection") as (receive, process):
        while True:
            live()
            if receive.poll(0.05):
                message = json.loads(receive.recv_bytes(4096))
                if not isinstance(message, dict) or set(message) != {"value"}:
                    raise RuntimeError("artifact inspection failed")
                result = message["value"]
                if (not isinstance(result, dict) or set(result) != {"bytes", "sha256"}
                        or type(result["bytes"]) is not int or not 0 <= result["bytes"] <= MAX_FILE
                        or not isinstance(result["sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", result["sha256"]) is None):
                    raise ValueError("invalid artifact metadata")
                break
            if not process.is_alive():
                raise RuntimeError("artifact inspection worker exited")
    live()
    return result
