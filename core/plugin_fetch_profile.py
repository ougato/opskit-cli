"""Supervised, private authentication profile input; never log profile bytes."""
import base64
import hashlib
import math
from pathlib import Path
import time
import uuid

from core.plugin_fetch_source import authentication, remote_source
from core.plugin_json import loads

MAX_PROFILE_BYTES = 65536
CHUNK_BYTES = 16384


def _profile_worker(connection, source, filename, request):
    from core.cli_secrets import _read_secret_bytes
    from network.worker import send
    try:
        raw = _read_secret_bytes(Path(filename))
        profile = loads(raw.decode("utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("profile object required")
        authentication(remote_source(source), profile)
        for offset in range(0, len(raw), CHUNK_BYTES):
            send(connection, {"request": request, "page": offset // CHUNK_BYTES,
                              "data": base64.b64encode(raw[offset:offset + CHUNK_BYTES]).decode("ascii")})
        send(connection, {"request": request, "done": True, "bytes": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest()})
    except (OSError, ValueError, RuntimeError):
        send(connection, {"request": request, "rejected": True})
    finally:
        connection.close()


def load_profile(source, filename, *, timeout):
    """Read only a named private file under one owned-process deadline.

    Reuses the secret input descriptor/identity/ACL checks. No chmod, environment
    credentials, SSH config, agent, stdin fallback or plugin-supplied path is
    consulted. Caller owns the returned sensitive dictionary. Same-account or
    privileged attackers remain outside this private-file ownership guarantee.
    """
    from network.worker import owned_process, wait_result
    source = remote_source(source)
    if (not isinstance(filename, str) or not filename or filename == "-"
            or len(filename.encode("utf-8")) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("invalid fetch profile input")
    deadline = time.monotonic() + timeout
    request = uuid.uuid4().hex
    raw, pages = bytearray(), 0
    with owned_process(_profile_worker, (source.url, filename, request), label="fetch profile") as (receive, _):
        while True:
            result = wait_result(receive, deadline=deadline, label="fetch profile")
            if result == {"request": request, "rejected": True}:
                raise ValueError("fetch profile input rejected")
            if result.get("request") != request:
                raise RuntimeError("invalid fetch profile response binding")
            if set(result) == {"request", "page", "data"}:
                try:
                    if (type(result["page"]) is not int or result["page"] != pages
                            or not isinstance(result["data"], str) or len(result["data"]) > 21848):
                        raise ValueError()
                    chunk = base64.b64decode(result["data"], validate=True)
                    if not 1 <= len(chunk) <= CHUNK_BYTES or len(raw) + len(chunk) > MAX_PROFILE_BYTES:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise RuntimeError("invalid fetch profile response page") from None
                raw.extend(chunk)
                pages += 1
                continue
            if (set(result) != {"request", "done", "bytes", "sha256"} or result["done"] is not True
                    or type(result["bytes"]) is not int or result["bytes"] != len(raw)
                    or result["sha256"] != hashlib.sha256(raw).hexdigest()):
                raise RuntimeError("invalid fetch profile response result")
            try:
                profile = loads(raw.decode("utf-8"))
                if not isinstance(profile, dict):
                    raise ValueError("profile object required")
                authentication(source, profile)
            except (ValueError, TypeError):
                raise RuntimeError("invalid fetch profile response content") from None
            if time.monotonic() >= deadline:
                raise TimeoutError("fetch profile deadline exceeded")
            return profile
