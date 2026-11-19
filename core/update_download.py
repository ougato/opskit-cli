"""Supervised checksum-verified downloads, published by one atomic candidate pointer.

The disposable worker holds the existing staging lock and uses the
existing downloader in a unique private directory, never the active candidate.
Only a complete verified generation is made visible to apply/startup.
"""
import math
import os
from pathlib import Path
import re
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
    from core.secrets import restrict_permissions
    from network.worker import send

    staging_token = None
    phase = "query"
    logging_disabled = updater._log.disabled
    updater._log.disabled = True
    readonly_token = updater._query_readonly.set(True)
    rollout_token = updater._initialize_rollout_identity.set(True)
    try:
        _remaining(deadline)
        cfg = update_cfg if update_cfg is not None else load_config(max_bytes=65536).get("update", {})
        if not isinstance(cfg, dict):
            raise ValueError("invalid update configuration")
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
        phase = "checksum_source"
        digest = (digest or updater._get_sha256_from_url(url + ".sha256")
                  or updater._get_release_digest(url, build, cfg))
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise ValueError("valid update checksum is required")
        digest = digest.lower()
        _remaining(deadline)
        root_cache = Path(root_cache)
        try:
            existing = updater._read_update_record(root_cache)
        except FileNotFoundError:
            existing = {}
        active = updater._get_pending_path()
        previous = existing.get("pending_version")
        if previous is not None and (type(previous) is not int or previous <= 0):
            raise ValueError("invalid existing update candidate")
        if previous is not None and build < previous:
            raise ValueError("update candidate is older than pending update")
        identity = existing.get("candidate_id")
        if (previous == build and identity and updater._verify_binary(active)
                and updater._verify_pending_checksum(active, build, expected_digest=digest)):
            _remaining(deadline)
            send(connection, {"ok": True, "data": _downloaded(build, identity)})
            return
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
        phase = "checksum"
        updater._update_record_path(pending)
        actual_digest = updater._sha256_file(pending)
        if actual_digest != digest:
            raise ValueError("update checksum mismatch before publication")
        _remaining(deadline)
        phase = "persistence"
        restrict_permissions(pending)
        with pending.open("r+b") as handle:
            os.fsync(handle.fileno())
        updater._write_update_record(staging / "candidate.json", {
            "schema_version": 1, "candidate_id": identity, "build": build,
            "sha256": actual_digest,
            "state": "verified"})
        _remaining(deadline)
        phase = "publication"
        # Last write is the publication point. Interruption before it leaves
        # the previous pointer intact; interruption after it leaves a complete
        # verified generation, even when the response cannot reach the parent.
        updater._write_update_record(root_cache, {**existing, "candidate_id": identity,
            "pending_version": build, "latest": build})
        send(connection, {"ok": True, "data": _downloaded(build, identity)})
    except Exception:
        # Never expose URLs, API bodies, configuration or transport exceptions.
        send(connection, {"ok": False, "error": "update download failed during " + phase +
                          "; inspect update status before retrying"})
    finally:
        if staging_token is not None:
            updater._download_staging.reset(staging_token)
        updater._initialize_rollout_identity.reset(rollout_token)
        updater._query_readonly.reset(readonly_token)
        updater._log.disabled = logging_disabled
        connection.close()


def _downloaded(build, identity):
    return {"available": True, "candidate_version": build, "candidate_id": identity,
            "checksum_verified": True, "state": "downloaded", "applied": False}


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
