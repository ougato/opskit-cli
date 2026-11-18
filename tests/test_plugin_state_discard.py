"""Only an invocation-owned, unjournaled candidate may be reclaimed."""
import base64
import io
import json
import os
from pathlib import Path
import tarfile
import time
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import publish_marker
from state_publication_fixture import state_packet

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_io as transaction


def owned(tmp_path):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    wrapper = root.parent / ".state-owned"
    candidate = wrapper / "new"
    candidate.mkdir(parents=True)
    (candidate / "value").write_bytes(b"new")
    lease = transaction.OwnedStaging(root, wrapper, state._directory(root.parent), state._directory(wrapper))
    return lease


def blocked_discard_worker(connection, *args):
    original = Path.unlink
    def unlink(path, *arguments, **kwargs):
        if path.parent.name == "new":
            read, write = os.pipe()
            publish_marker(os.environ["OPSKIT_TEST_DISCARD_PID"], str(os.getpid()))
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
        return original(path, *arguments, **kwargs)
    Path.unlink = unlink
    transaction._discard_worker(connection, *args)


def malformed_discard_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        result.update(json.loads(os.environ["OPSKIT_TEST_DISCARD_RESULT"]))
        original(connection, result)
    worker.send = send
    transaction._discard_worker(connection, *args)


@pytest.mark.parametrize("change", [{"request": "stale"}, {"root": "/other"},
                                    {"wrapper": "/other"}, {"ok": 1},
                                    {"outcome": "committed"}, {"extra": True}])
def test_invalid_discard_result_is_never_accepted(tmp_path, monkeypatch, change):
    lease = owned(tmp_path)
    monkeypatch.setenv("OPSKIT_TEST_DISCARD_RESULT", json.dumps(change))
    monkeypatch.setattr(transaction, "_discard_worker", malformed_discard_worker)
    with pytest.raises(RuntimeError, match="invalid state staging cleanup result"):
        transaction.discard_supervised(lease, timeout=30)
    assert (lease.root / "value").read_bytes() == b"old"
    assert lease.retired is False


def test_live_transaction_guard_prevents_discard(tmp_path):
    lease = owned(tmp_path)
    with state.transaction_guard(lease.root):
        with pytest.raises(RuntimeError, match="cleanup refused"):
            transaction.discard_supervised(lease, timeout=30)
    assert (lease.candidate / "value").read_bytes() == b"new"
    assert transaction.discard_supervised(lease, timeout=30) == "removed"


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_discard_is_reaped_and_retained_candidate_retries(tmp_path, monkeypatch,
                                                                expire_worker_after_marker, cancel):
    from network import worker
    lease = owned(tmp_path)
    marker = tmp_path / "discard-pid"
    monkeypatch.setenv("OPSKIT_TEST_DISCARD_PID", str(marker))
    with monkeypatch.context() as patch:
        patch.setattr(transaction, "_discard_worker", blocked_discard_worker)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
        else:
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            transaction.discard_supervised(lease, timeout=30)
    monkeypatch.setattr(worker, "time", time)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert (lease.candidate / "value").read_bytes() == b"new"
    assert (lease.root / "value").read_bytes() == b"old"
    assert transaction.discard_supervised(lease, timeout=30) == "removed"
    assert not lease.wrapper.exists()
    assert transaction.discard_supervised(lease, timeout=30) == "absent"


def test_journal_and_unknown_siblings_are_never_discarded(tmp_path):
    lease = owned(tmp_path)
    unknown = lease.root.parent / ".state-unknown"
    unknown.mkdir()
    (unknown / "evidence").write_bytes(b"keep")
    state.prepare_record(lease.root, lease.candidate, plugin="owner", caller="platform")
    assert transaction.discard_supervised(lease, timeout=30) == "retained"
    assert (lease.candidate / "value").read_bytes() == b"new"
    assert (unknown / "evidence").read_bytes() == b"keep"
    assert state.record_path(lease.root).exists()


@pytest.mark.parametrize("change", ["identity", "unknown", "hardlink", "parent"])
def test_ambiguous_tree_is_refused_before_any_deletion(tmp_path, change):
    lease = owned(tmp_path)
    if change == "identity":
        lease.wrapper.rename(lease.wrapper.with_name("retained"))
        lease.wrapper.mkdir()
        (lease.wrapper / "foreign").write_bytes(b"keep")
    elif change == "unknown":
        (lease.wrapper / "foreign").write_bytes(b"keep")
    elif change == "parent":
        lease.parent_identity = [0, 1]
    else:
        os.link(lease.candidate / "value", lease.root.parent / "other-link")
    with pytest.raises(RuntimeError, match="cleanup refused"):
        transaction.discard_supervised(lease, timeout=30)
    assert lease.wrapper.exists()
    assert (lease.root / "value").read_bytes() == b"old"
    if change != "identity":
        assert (lease.candidate / "value").read_bytes() == b"new"


@pytest.mark.parametrize("primary", [KeyboardInterrupt, TimeoutError, ValueError])
def test_cleanup_failure_does_not_replace_primary_error(tmp_path, monkeypatch, primary):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    def fail(*args, **kwargs):
        raise TimeoutError("injected cleanup deadline")
    monkeypatch.setattr(transaction, "discard_supervised", fail)
    error = primary("primary")
    with pytest.raises(primary) as caught:
        with sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform") as lease:
            raise error
    assert caught.value is error
    assert lease.candidate.exists()


def test_cleanup_failure_without_primary_is_reported(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    def fail(*args, **kwargs):
        raise TimeoutError("injected cleanup deadline")
    monkeypatch.setattr(transaction, "discard_supervised", fail)
    with pytest.raises(sandbox.SandboxError, match="cleanup incomplete; evidence retained"):
        with sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform") as lease:
            pass
    assert lease.candidate.exists()


def test_successful_publication_does_not_launch_redundant_discard(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    from core.plugin_trust import grant
    grant("owner", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("owner", "a" * 64),), raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("committed transaction launched redundant discard")
    monkeypatch.setattr(transaction, "discard_supervised", forbidden)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("value")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"new"))
    sandbox._save_state(base64.b64encode(output.getvalue()).decode("ascii"), root,
                        plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    assert not state.record_path(root).exists()
    assert not list(root.parent.glob(".state-*"))


def test_parent_does_not_scan_or_remove_candidate(tmp_path, monkeypatch):
    lease = owned(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("candidate cleanup ran in parent")
    monkeypatch.setattr(state, "_remove_owned_tree", forbidden)
    assert transaction.discard_supervised(lease, timeout=30) == "removed"
    assert not lease.wrapper.exists()
