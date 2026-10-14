"""Supervised signed downloads, published by one atomic candidate pointer.

The disposable worker holds the existing staging lock and uses the
existing downloader in a unique private directory, never the active candidate.
Only a complete verified generation is made visible to apply/startup.
"""
import math
import os
from pathlib import Path
import time
from uuid import uuid4


def _remaining(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("update download deadline exceeded")


def _worker(connection, root_cache, deadline, update_cfg):
    from core.resource_lock import ResourceLock
    from network.worker import send
    resource = ResourceLock(lambda: Path(root_cache).parent / "locks" / "update.lock",
                            label="update staging area")
    try:
        with resource.hold():
            _pipeline(connection, root_cache, deadline, update_cfg)
    except Exception:
        send(connection, {"ok": False, "error": "update download lock or worker failed"})
        connection.close()


def _pipeline(connection, root_cache, deadline, update_cfg):
    from core import updater
    from core.config import load_config
    from core.constants import FILE_UPDATE_CACHE
    from core.release_signature import verify
    from core.secrets import restrict_permissions
    from network.worker import send

    staging_token = None
    phase = "query"
    logging_disabled = updater._log.disabled
    updater._log.disabled = True
    readonly_token = updater._query_readonly.set(True)
    try:
        _remaining(deadline)
        cfg = update_cfg if update_cfg is not None else load_config(max_bytes=65536).get("update", {})
        if not isinstance(cfg, dict) or not updater._signing_key(cfg):
            raise ValueError("update signing key is not configured")
        target = updater.query_update_target(cfg)
        _remaining(deadline)
        if target is None:
            send(connection, {"ok": True, "data": {"available": False, "reason": "no_eligible_update"}})
            return
        build, url, digest = target
        if type(build) is not int or build <= updater.APP_VERSION:
            raise ValueError("invalid update candidate version")
        from core.update_target_state import validate_upgrade
        phase = "version_policy"
        validate_upgrade(updater._self_path(), build)
        _remaining(deadline)
        root_cache = Path(root_cache)
        try:
            existing = updater._read_update_record(root_cache)
        except FileNotFoundError:
            existing = {}
        updater._get_pending_path()
        previous = existing.get("pending_version")
        if previous is not None and (type(previous) is not int or previous <= 0):
            raise ValueError("invalid existing update candidate")
        if previous is not None and build < previous:
            raise ValueError("update candidate is older than pending update")
        phase = "cache_quota"
        from core.update_cache import reserve_candidate
        reserve_candidate(root_cache, updater.MAX_UPDATE_BYTES + 131072,
                          checkpoint=lambda: _remaining(deadline))
        identity = uuid4().hex
        staging = root_cache.parent / "update-candidates" / identity
        updater._update_record_path(staging / FILE_UPDATE_CACHE)
        staging.mkdir(parents=True, exist_ok=False)
        restrict_permissions(staging.parent)
        restrict_permissions(staging)
        updater._write_update_record(staging / "candidate.json", {
            "schema_version": 1, "candidate_id": identity, "build": build,
            "sha256": digest.lower() if digest else None, "state": "preparing"})
        staging_token = updater._download_staging.set(staging)
        phase = "download"
        pending = updater._download_update(url, digest, build)
        _remaining(deadline)
        if pending is None or not updater._verify_binary(pending):
            raise ValueError("update download or binary validation failed")
        phase = "signature"
        if not updater._fetch_release_signature(pending, url, build, cfg):
            raise ValueError("update signature verification failed")
        _remaining(deadline)
        updater._update_record_path(pending)
        sidecar = pending.with_name(pending.name + ".sig.json")
        envelope = updater._read_update_record(sidecar)
        actual_digest = updater._sha256_file(pending)
        if not verify(envelope, updater._signing_key(cfg), build=build,
                      filename=updater._asset_filename(), digest=actual_digest):
            raise ValueError("update signature verification failed")
        phase = "persistence"
        for artifact in (pending, sidecar):
            restrict_permissions(artifact)
            with artifact.open("r+b") as handle:
                os.fsync(handle.fileno())
        updater._write_update_record(staging / "candidate.json", {
            "schema_version": 1, "candidate_id": identity, "build": build,
            "sha256": actual_digest, "state": "verified"})
        _remaining(deadline)
        phase = "publication"
        # Last write is the publication point. Interruption before it leaves
        # the previous pointer intact; interruption after it leaves a complete
        # signed generation, even when the response cannot reach the parent.
        updater._write_update_record(root_cache, {**existing, "candidate_id": identity,
            "pending_version": build, "latest": build})
        send(connection, {"ok": True, "data": {"available": True, "candidate_version": build,
            "candidate_id": identity, "signature_verified": True, "state": "downloaded",
            "applied": False}})
    except Exception:
        # Never expose URLs, API bodies, configuration or transport exceptions.
        send(connection, {"ok": False, "error": "update download failed during " + phase +
                          "; inspect update status before retrying"})
    finally:
        if staging_token is not None:
            updater._download_staging.reset(staging_token)
        updater._query_readonly.reset(readonly_token)
        updater._log.disabled = logging_disabled
        connection.close()


def download(*, timeout=300, update_cfg=None, cancel_event=None):
    from core import updater
    from network.worker import run
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("update timeout must be positive")
    if not updater._is_frozen():
        raise ValueError("binary updates require a packaged executable")
    deadline = time.monotonic() + timeout
    result = run(_worker, (str(updater._get_cache_path()), deadline, update_cfg),
                 timeout=timeout, label="update download", cancel_event=cancel_event)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "update download failed"))
    return result["data"]
