"""Download, verify and activate managed directory releases."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import tempfile
import time

import httpx

from core.directory_archive import read_manifest, verify_installed_tree
from core.directory_install import Install, InstallError, managed_install
from core.i18n import t


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("directory update deadline exceeded")
    return seconds


def _sidecar(client, url, filename, deadline):
    from core import updater
    import re

    with updater._https_update_stream(url + ".sha256", client=client, timeout=_remaining(deadline)) as response:
        if response.status_code == 404:
            return ""
        fields = updater._small_update_body(response).decode("ascii").split()
    if (len(fields) != 2 or fields[1].removeprefix("*") != filename
            or not re.fullmatch(r"[a-fA-F0-9]{64}", fields[0])):
        raise InstallError("invalid directory update checksum")
    return fields[0].lower()


def _release_digests(client, origin, version, cfg, deadline):
    """Use official sidecars first, with GitHub's independent asset metadata available as fallback."""
    from core import updater

    repo = cfg.get("repo", "ougato/opskit-cli")
    expected = f"{updater.GITHUB_BASE}/{repo}/releases/download/v{version}/"
    if origin + "/" != expected:
        raise InstallError("directory update origin did not match the release tag")
    release = None

    def resolve(filename):
        nonlocal release
        url = expected + filename
        try:
            sidecar = _sidecar(client, url, filename, deadline)
        except (httpx.HTTPError, OSError, ValueError, RuntimeError):
            sidecar = ""
        if sidecar:
            return sidecar
        if release is None:
            endpoint = updater.GITHUB_API_RELEASES.format(repo=repo).removesuffix("/latest") + f"/tags/v{version}"
            token = cfg.get("github_token")
            headers = {"Authorization": f"token {token}"} if token else None
            try:
                _remaining(deadline)
                release = updater._readonly_json(client, endpoint, headers=headers)
            except (httpx.HTTPError, OSError, ValueError, RuntimeError):
                release = {}
        if release.get("tag_name") == f"v{version}" and release.get("draft") is False:
            return updater._github_asset_digest(release, filename, url)
        return ""

    return resolve


def _download(client, url, expected, destination, deadline):
    from core import updater

    for source in updater._release_urls(url):
        try:
            digest = hashlib.sha256()
            size = 0
            with updater._https_update_stream(source, client=client, timeout=min(15, _remaining(deadline))) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for data in response.iter_raw(chunk_size=262144):
                        _remaining(deadline)
                        size += len(data)
                        if size > updater.MAX_UPDATE_BYTES:
                            raise InstallError("directory update exceeds download size limit")
                        output.write(data)
                        digest.update(data)
            if size and digest.hexdigest() == expected:
                return size
        except (httpx.HTTPError, OSError, ValueError):
            _remaining(deadline)
    raise InstallError("directory update download or checksum verification failed")


def _download_and_prepare(install, state, build, asset_url, cfg, deadline):
    origin, filename = asset_url.rsplit("/", 1)
    stem = filename.removesuffix(".exe")
    full_name = f"{stem}.tar.xz"
    delta_name = f"{stem}-from-v{state.active.version}.tar.xz"
    with tempfile.TemporaryDirectory(dir=install.root, prefix=".download-") as temp, \
            httpx.Client(timeout=min(10, _remaining(deadline))) as client:
        payload = Path(temp) / "package.tar.xz"
        digest = _release_digests(client, origin, build, cfg, deadline)
        delta_digest = digest(delta_name)
        if delta_digest:
            try:
                transferred = _download(client, f"{origin}/{delta_name}", delta_digest, payload, deadline)
                if read_manifest(payload).base_version != state.active.version:
                    raise InstallError("directory increment does not match the installed version")
                candidate = install.prepare(payload, build, base=install.tree(state.active))
                return candidate, "incremental", transferred
            except (OSError, ValueError):
                _remaining(deadline)
        full_digest = digest(full_name)
        if not full_digest:
            raise InstallError("directory release checksum unavailable")
        transferred = _download(client, f"{origin}/{full_name}", full_digest, payload, deadline)
        if read_manifest(payload).base_version is not None:
            raise InstallError("expected a complete directory release")
        return install.prepare(payload, build), "full", transferred


def _stage_locked(install, *, update_cfg, expected_version, deadline):
    from core import updater
    from core.config import load_config

    cfg = update_cfg if update_cfg is not None else load_config(max_bytes=65536).get("update", {})
    if not isinstance(cfg, dict):
        raise InstallError("invalid update configuration")
    state = install.state()
    target = updater.query_update_target(cfg)
    _remaining(deadline)
    if not target or target[0] <= state.active.version:
        return {"available": False, "reason": "no_eligible_update"}
    build, asset_url, _ = target
    if (expected_version is not None and expected_version != build
            or state.staged is not None and state.staged.version > build):
        return {"available": False, "reason": "target_changed"}
    if state.rejected is not None and build <= state.rejected:
        return {"available": False, "reason": "rolled_back_version"}
    if state.staged is not None and state.staged.version == build:
        candidate, transport, transferred = state.staged, "cached", 0
    else:
        candidate, transport, transferred = _download_and_prepare(install, state, build, asset_url, cfg, deadline)
        old_staged = state.staged
        state.staged = candidate
        state.write(install.state_path)
        if old_staged is not None:
            try:
                install.discard(old_staged)
            except OSError:
                pass
    return {"available": True, "checksum_verified": True, "candidate_version": build,
            "candidate_id": candidate.name, "state": "downloaded", "applied": False,
            "transport": transport, "download_bytes": transferred}


def _stage_worker(connection, entry, cfg, expected_version, deadline):
    from network.worker import send

    try:
        install = Install(Path(entry))
        with install.lock.hold():
            data = _stage_locked(install, update_cfg=cfg, expected_version=expected_version, deadline=deadline)
        send(connection, {"ok": True, "data": data})
    except (OSError, RuntimeError, ValueError):
        # Transport and filesystem exceptions may include configuration details.
        send(connection, {"ok": False})
    finally:
        connection.close()


def stage(install, *, timeout=300, update_cfg=None, expected_version=None):
    from network.worker import run

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(t("update.directory_bad_timeout"))
    if not install.can_write():
        raise RuntimeError(t("update.directory_permissions"))
    result = run(_stage_worker, (str(install.entry), update_cfg, expected_version, time.monotonic() + timeout),
                 timeout=timeout, label="directory update")
    if not result.get("ok"):
        raise RuntimeError(t("update.directory_download_failed"))
    return result["data"]


def activate(install, *, expected_candidate_id=None):
    if not install.can_write():
        raise RuntimeError(t("update.directory_permissions"))
    with install.lock.hold():
        state = install.state()
        candidate = state.staged
        if candidate is None or expected_candidate_id is not None and candidate.name != expected_candidate_id:
            raise RuntimeError(t("update.install_candidate_changed"))
        state.staged = None
        try:
            verify_installed_tree(install.tree(candidate), version=candidate.version, tree_sha256=candidate.tree_sha256)
            healthy = install.healthy(candidate)
        except (OSError, ValueError):
            healthy = False
        if not healthy:
            state.rejected = max(state.rejected or 0, candidate.version)
            state.write(install.state_path)
            try:
                install.discard(candidate)
            except OSError:
                pass
            raise RuntimeError(t("update.directory_candidate_failed", version=candidate.version))
        state.previous, state.active = state.active, candidate
        state.write(install.state_path)
        return {"state": "applied", "version": candidate.version, "applied": True, "installation": "directory"}


def status() -> dict | None:
    from core.constants import APP_VERSION

    install = managed_install()
    if install is None:
        return None
    state = install.state()
    return {"installation": "directory", "current_version": APP_VERSION, "selected_version": state.active.version,
            "binary_update_supported": True,
            "pending_present": state.staged is not None, "pending_verification": "not_checked",
            "pending_version": state.staged.version if state.staged else None,
            "previous_version": state.previous.version if state.previous else None,
            "rejected_version": state.rejected}
