"""Candidate files never escape private owned staging or bypass supervision."""
from dataclasses import replace
import json
import os
import pickle

import pytest

from core import plugin_fetch_prepare as preparation, plugin_workspace as workspace
from core.private_file import temporary_private_file
from test_plugin_fetch_candidate import archive, response
from worker_test_markers import publish_process_marker, marked_process_exists


@pytest.fixture
def lease():
    value = workspace.allocate(timeout=30)
    try:
        yield value
    finally:
        workspace.cleanup(value, timeout=30)


def prepare(lease, entries=None):
    entries = entries if entries is not None else [("src/plugin.py", b"raw", {"mode": 0o755})]
    with temporary_private_file() as source:
        source.write(response(archive(entries), entries=len(entries), size=sum(len(e[1]) for e in entries)))
        source.flush()
        return preparation.prepare_candidate(source, lease, timeout=30)


def test_real_owned_worker_writes_exact_private_candidate_and_preserves_modes(lease):
    entries = [("src/猫.py", b"raw", {"mode": 0o755}), (".gitattributes", b"filter=demo", {})]
    result = prepare(lease, entries)
    assert result.root == lease.root / "candidate"
    assert result.files == 2 and result.bytes == 14 and result.reported_commit == "a" * 40
    assert (result.root / "src/猫.py").read_bytes() == b"raw"
    assert (result.root / ".gitattributes").read_bytes() == b"filter=demo"
    from core.plugin_state import _directory
    assert _directory(result.root) == result.identity
    if os.name != "nt":
        assert (result.root / "src/猫.py").stat().st_mode & 0o777 == 0o700
        assert (result.root / ".gitattributes").stat().st_mode & 0o777 == 0o600
    else:
        from core.private_file import verify_private_file
        with (result.root / ".gitattributes").open("rb") as content:
            verify_private_file(content.fileno())


@pytest.mark.parametrize("name", ["../outside", ".git/config", "NUL.txt", "a/b/../../outside"])
def test_invalid_layout_is_rejected_before_first_candidate_write(lease, name):
    with pytest.raises(ValueError, match="preparation rejected"):
        prepare(lease, [(name, b"raw", {})])
    assert list(lease.root.iterdir()) == []


def test_retained_workspace_is_not_overwritten_or_extended(lease):
    (lease.root / "evidence").write_bytes(b"keep")
    with pytest.raises(ValueError, match="preparation rejected"):
        prepare(lease)
    assert list(lease.root.iterdir()) == [lease.root / "evidence"]
    assert (lease.root / "evidence").read_bytes() == b"keep"


def test_second_preparation_cannot_replace_existing_candidate(lease):
    result = prepare(lease)
    with pytest.raises(ValueError, match="preparation rejected"):
        prepare(lease, [("src/plugin.py", b"bad", {})])
    assert (result.root / "src/plugin.py").read_bytes() == b"raw"


def test_wrong_workspace_identity_refuses_writes(lease):
    changed = replace(lease, identity=[lease.identity[0], lease.identity[1] + 1])
    with pytest.raises(ValueError, match="preparation rejected"):
        prepare(changed)
    assert list(lease.root.iterdir()) == []


def test_wrong_namespace_refuses_before_worker(lease, tmp_path):
    changed = replace(lease, root=tmp_path / "outside")
    with pytest.raises(ValueError, match="workspace"):
        prepare(changed)
    assert not changed.root.exists()


def test_excessive_depth_rejected_before_allocation(lease):
    name = "/".join(["x"] * 64 + ["file"])
    with pytest.raises(ValueError, match="preparation rejected"):
        prepare(lease, [(name, b"raw", {})])
    assert list(lease.root.iterdir()) == []


def blocked_worker(connection, *args):
    stage = os.environ["OPSKIT_TEST_FETCH_PREPARE_STAGE"]
    original = preparation.validated_archive if stage == "validate" else preparation._write_file
    def block(*args, **kwargs):
        read, write = os.pipe()
        publish_process_marker(os.environ["OPSKIT_TEST_FETCH_PREPARE_MARKER"])
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
        return original(*args, **kwargs)
    if stage == "validate":
        preparation.validated_archive = block
    else:
        preparation._write_file = block
    preparation._prepare_worker(connection, *args)


@pytest.mark.parametrize("stage", ["validate", "write"])
def test_blocked_phase_expires_and_worker_is_gone(lease, tmp_path, monkeypatch, expire_worker_after_marker, stage):
    marker = tmp_path / "worker.json"
    monkeypatch.setenv("OPSKIT_TEST_FETCH_PREPARE_STAGE", stage)
    monkeypatch.setenv("OPSKIT_TEST_FETCH_PREPARE_MARKER", str(marker))
    monkeypatch.setattr(preparation, "_prepare_worker", blocked_worker)
    budget, observed = expire_worker_after_marker(marker)
    with temporary_private_file() as source:
        source.write(response(archive([("file", b"raw", {})]))); source.flush()
        with pytest.raises(TimeoutError):
            preparation.prepare_candidate(source, lease, timeout=budget)
        assert not source.closed
    assert observed and not marked_process_exists(marker)
    if stage == "validate":
        assert list(lease.root.iterdir()) == []
    else:
        assert (lease.root / "candidate").is_dir()


def malformed_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        if result.get("ok"):
            result.update(json.loads(os.environ["OPSKIT_TEST_FETCH_PREPARE_RESULT"]))
        original(connection, result)
    worker.send = send
    preparation._prepare_worker(connection, *args)


@pytest.mark.parametrize("change", [{"request": "stale"}, {"identity": [True, 1]},
                                    {"files": True}, {"archive": "bad"}, {"extra": 1}])
def test_malformed_result_never_authorizes_candidate(lease, monkeypatch, change):
    monkeypatch.setenv("OPSKIT_TEST_FETCH_PREPARE_RESULT", json.dumps(change))
    monkeypatch.setattr(preparation, "_prepare_worker", malformed_worker)
    with pytest.raises(RuntimeError, match="invalid candidate preparation result"):
        prepare(lease)
    assert (lease.root / "candidate/src/plugin.py").read_bytes() == b"raw"


def test_file_transfer_cannot_start_resource_sharer_outside_spawn():
    with temporary_private_file() as source:
        with pytest.raises(RuntimeError, match="owned spawn"):
            pickle.dumps(preparation._SpawnFile(source.fileno()))


def replacing_worker(connection, source, lease, request):
    original = preparation._write_file
    replaced = []
    def replace_root(*args, **kwargs):
        if not replaced:
            replaced.append(True)
            lease.root.rename(lease.root.with_name(lease.root.name + "-retained"))
            lease.root.symlink_to(os.environ["OPSKIT_TEST_FETCH_PREPARE_OUTSIDE"], target_is_directory=True)
        return original(*args, **kwargs)
    preparation._write_file = replace_root
    preparation._prepare_worker(connection, source, lease, request)


def test_root_replacement_does_not_write_into_redirected_directory(lease, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    (outside / "candidate/src").mkdir(parents=True)
    (outside / "candidate/src/plugin.py").write_bytes(b"keep")
    monkeypatch.setenv("OPSKIT_TEST_FETCH_PREPARE_OUTSIDE", str(outside))
    monkeypatch.setattr(preparation, "_prepare_worker", replacing_worker)
    retained = lease.root.with_name(lease.root.name + "-retained")
    try:
        with pytest.raises(ValueError, match="preparation rejected"):
            prepare(lease)
        assert (outside / "candidate/src/plugin.py").read_bytes() == b"keep"
    finally:
        # Only the synthetic replacement and the exact fixture-owned directory.
        if retained.exists():
            lease.root.unlink()
            retained.rename(lease.root)


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_budget_is_rejected_without_writes(lease, timeout):
    with temporary_private_file() as source:
        with pytest.raises(ValueError, match="invalid candidate"):
            preparation.prepare_candidate(source, lease, timeout=timeout)
    assert list(lease.root.iterdir()) == []
