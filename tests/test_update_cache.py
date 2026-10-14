"""Candidate quotas preserve active/retry generations and unknown user content."""
import hashlib
import os
from pathlib import Path

import pytest

from core import updater, update_cache


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    return updater._get_cache_path()


def candidate(cache, number, *, state="verified", content=b"candidate"):
    identity = f"{number:032x}"
    root = cache.parent / "update-candidates" / identity
    root.mkdir(parents=True)
    (root / "opskit.pending").write_bytes(content)
    updater._write_update_record(root / "candidate.json", {
        "schema_version": 1, "candidate_id": identity, "build": updater.APP_VERSION + 1,
        "state": state, "sha256": hashlib.sha256(content).hexdigest()})
    os.utime(root, ns=(number * 1000000000, number * 1000000000))
    return root


def reserve(cache, incoming=100):
    with updater._update_resource.hold():
        return update_cache.reserve_candidate(cache, incoming)


def test_count_quota_preserves_active_and_marker_candidates(cache):
    roots = [candidate(cache, number) for number in range(1, 6)]
    updater._write_update_record(cache, {"candidate_id": roots[0].name, "pending_version": updater.APP_VERSION + 1})
    updater._write_update_record(cache.parent / "update_pending_path.json", {"pending": str(roots[1] / "opskit.pending")})
    before = cache.read_bytes()
    result = reserve(cache)
    assert result["removed"] == 2 and result["retained"] == 3
    assert roots[0].exists() and roots[1].exists() and roots[4].exists()
    assert not roots[2].exists() and not roots[3].exists()
    assert cache.read_bytes() == before
    assert updater._get_pending_path().read_bytes() == b"candidate"


def test_failed_owned_staging_is_reclaimed_even_below_quota(cache):
    failed = candidate(cache, 1, state="preparing")
    (failed / "opskit.pending.tmp").write_bytes(b"partial")
    result = reserve(cache)
    assert result == {"removed": 1, "retained": 0, "bytes": 0}
    assert not failed.exists()


def test_byte_quota_counts_metadata_and_preserves_active(cache, monkeypatch):
    old = candidate(cache, 1)
    active = candidate(cache, 2)
    updater._write_update_record(cache, {"candidate_id": active.name})
    total = sum(path.stat().st_size for node in (old, active) for path in node.iterdir())
    monkeypatch.setattr(update_cache, "MAX_CANDIDATE_BYTES", total + 99)
    result = reserve(cache, incoming=100)
    assert result["removed"] == 1 and result["retained"] == 1
    assert active.exists() and not old.exists()
    assert result["bytes"] + 100 <= update_cache.MAX_CANDIDATE_BYTES


def test_unknown_user_file_is_preserved_and_blocks_unsafe_reclamation(cache, monkeypatch):
    root = candidate(cache, 1, state="preparing")
    secret = root / "user-notes.txt"
    secret.write_text("private-user-data")
    monkeypatch.setattr(update_cache, "MAX_CANDIDATES", 1)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        reserve(cache)
    assert secret.read_text() == "private-user-data"
    assert (root / "candidate.json").exists()
    assert (root / "opskit.pending").exists()


def test_unowned_uuid_directory_is_not_deleted(cache, monkeypatch):
    root = cache.parent / "update-candidates" / ("a" * 32)
    root.mkdir(parents=True)
    (root / "opskit.pending").write_text("unowned-data")
    monkeypatch.setattr(update_cache, "MAX_CANDIDATES", 1)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        reserve(cache)
    assert (root / "opskit.pending").read_text() == "unowned-data"


@pytest.mark.parametrize("field,value", [("candidate_id", "../escape"), ("build", True),
    ("schema_version", True), ("state", "unknown"), ("sha256", "bad")])
def test_damaged_ownership_prevents_all_cleanup(cache, field, value):
    first = candidate(cache, 1, state="preparing")
    second = candidate(cache, 2, state="preparing")
    record = updater._read_update_record(second / "candidate.json")
    updater._write_update_record(second / "candidate.json", {**record, field: value})
    with pytest.raises(ValueError, match="ownership"):
        reserve(cache)
    assert first.exists() and second.exists()


@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
def test_linked_artifact_cannot_delete_external_data(cache, tmp_path, link_type):
    root = candidate(cache, 1, state="preparing")
    outside = tmp_path / "outside-private-data"
    outside.write_text("untouched")
    artifact = root / "opskit.pending"
    artifact.unlink()
    if link_type == "symlink":
        try:
            artifact.symlink_to(outside)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    else:
        os.link(outside, artifact)
    with pytest.raises(ValueError):
        reserve(cache)
    assert outside.read_text() == "untouched"
    assert artifact.exists()
    assert (root / "candidate.json").exists()


def test_unknown_nested_directory_refuses_recursive_delete(cache):
    root = candidate(cache, 1, state="preparing")
    nested = root / "private-files"
    nested.mkdir()
    (nested / "credentials.txt").write_text("private")
    with pytest.raises(ValueError, match="nested"):
        reserve(cache)
    assert (nested / "credentials.txt").read_text() == "private"


def test_inventory_is_bounded_before_any_deletion(cache):
    for number in range(1, update_cache.MAX_INVENTORY_ENTRIES + 2):
        candidate(cache, number, state="preparing")
    with pytest.raises(ValueError, match="inventory exceeds"):
        reserve(cache)
    assert len(list((cache.parent / "update-candidates").iterdir())) == update_cache.MAX_INVENTORY_ENTRIES + 1


def test_unlink_failure_retains_owner_record_and_active_pointer(cache, monkeypatch):
    old = candidate(cache, 1, state="preparing")
    active = candidate(cache, 2)
    updater._write_update_record(cache, {"candidate_id": active.name})
    before = cache.read_bytes()
    unlink = Path.unlink
    def refused(path, *args, **kwargs):
        if path == old / "opskit.pending":
            raise OSError("injected unlink failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", refused)
    with pytest.raises(OSError, match="unlink failure"):
        reserve(cache)
    assert (old / "candidate.json").exists()
    assert (old / "opskit.pending").exists()
    assert cache.read_bytes() == before
    assert active.exists()


def test_invalid_marker_blocks_cleanup_instead_of_guessing_ownership(cache):
    root = candidate(cache, 1, state="preparing")
    updater._write_update_record(cache.parent / "update_pending_path.json", {"pending": "../private"})
    with pytest.raises(ValueError, match="retry candidate"):
        reserve(cache)
    assert root.exists()


def test_live_orphan_worker_keeps_root_locked_until_terminated(cache, monkeypatch):
    """A launcher exits abruptly; its disposable child still owns the OS lock."""
    import json
    import subprocess
    import sys
    import psutil
    cache.parent.mkdir(parents=True)
    updater._write_update_record(cache, {"pending_version": updater.APP_VERSION + 1})
    legacy = updater._get_pending_path()
    legacy.write_bytes(b"previous-candidate")
    before = cache.read_bytes()
    identity = cache.parent / "owned-worker.json"
    launcher = '''
import json, os, sys, time
from pathlib import Path
from multiprocessing import get_context
import psutil
sys.path.insert(0, sys.argv[3])
from test_update_download import slow_pipeline
context = get_context("spawn")
receive, transmit = context.Pipe(duplex=False)
process = context.Process(target=slow_pipeline, args=(transmit, sys.argv[1], time.monotonic()+60, {"signing_public_key":"test-key"}))
process.start()
transmit.close()
limit = time.monotonic()+10
while not Path(sys.argv[1]).with_suffix(".started").exists():
    if time.monotonic() >= limit:
        process.terminate()
        process.join(3)
        raise RuntimeError("owned worker did not reach download")
    time.sleep(.05)
Path(sys.argv[2]).write_text(json.dumps({"pid":process.pid,"created":psutil.Process(process.pid).create_time()}))
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", launcher, str(cache), str(identity), str(Path(__file__).parent.resolve())],
                   check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    info = json.loads(identity.read_text())
    process = psutil.Process(info["pid"])
    assert process.create_time() == info["created"]
    try:
        with pytest.raises(RuntimeError, match="busy"):
            reserve(cache)
        assert cache.read_bytes() == before and legacy.read_bytes() == b"previous-candidate"
        assert list((cache.parent / "update-candidates").glob("*/candidate.json"))
    finally:
        process.kill()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            assert process.status() == psutil.STATUS_ZOMBIE
    result = reserve(cache)
    assert result["removed"] == 1
    assert not list((cache.parent / "update-candidates").iterdir())
    assert cache.read_bytes() == before and legacy.read_bytes() == b"previous-candidate"
