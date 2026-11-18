"""Candidate writes are supervised independently from commit authorization."""
import base64
import io
import json
import os
import tarfile
import time
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import publish_marker

from core import plugin_sandbox as sandbox, plugin_state_prepare as preparation


def packet():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("value")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"new"))
    return base64.b64encode(output.getvalue()).decode("ascii")


def blocked_preparation_worker(connection, *args):
    from core import archive, secrets
    def blocked(*args, **kwargs):
        read, write = os.pipe()
        publish_marker(os.environ["OPSKIT_TEST_PREPARATION_PID"], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    if os.environ["OPSKIT_TEST_PREPARATION_STAGE"] == "extract":
        archive.extract_tar = blocked
    else:
        secrets.restrict_permissions = blocked
    preparation._prepare_worker(connection, *args)


def malformed_preparation_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        if "candidate" in result:
            result.update(json.loads(os.environ["OPSKIT_TEST_PREPARATION_RESULT"]))
        original(connection, result)
    worker.send = send
    preparation._prepare_worker(connection, *args)


@pytest.mark.parametrize("change", [
    {"request": "stale"}, {"wrapper": [0, 1]}, {"candidate": [False, 1]},
    {"candidate": [0, 1]}, {"extra": True}, {"ok": 1},
])
def test_invalid_or_stale_result_cannot_publish(tmp_path, monkeypatch, change):
    from core.plugin_trust import grant
    grant("owner", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("owner", "a" * 64),), raising=False)
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    monkeypatch.setenv("OPSKIT_TEST_PREPARATION_RESULT", json.dumps(change))
    monkeypatch.setattr(preparation, "_prepare_worker", malformed_preparation_worker)
    with pytest.raises(RuntimeError, match="preparation result|identity changed|publication outcome unconfirmed"):
        sandbox._save_state(packet(), root, plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"old"
    wrappers = list(root.parent.glob(".state-*"))
    if change == {"candidate": [0, 1]} and type(change["candidate"][0]) is int:
        # Shape-valid identities are rechecked against disk in the publication
        # worker. An uncertain publication preserves its owned evidence.
        assert len(wrappers) == 1 and (wrappers[0] / "new" / "value").read_bytes() == b"new"
    else:
        assert not wrappers


@pytest.mark.parametrize("stage", ["extract", "permissions"])
@pytest.mark.parametrize("cancel", [False, True])
def test_actual_blocked_preparation_is_reaped_before_cleanup(tmp_path, monkeypatch,
                                                           expire_worker_after_marker, stage, cancel):
    from network import worker
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    marker = tmp_path / "writer-pid"
    monkeypatch.setenv("OPSKIT_TEST_PREPARATION_PID", str(marker))
    monkeypatch.setenv("OPSKIT_TEST_PREPARATION_STAGE", stage)
    monkeypatch.setattr(preparation, "_prepare_worker", blocked_preparation_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    from core import plugin_state_io
    original = plugin_state_io.discard_supervised
    def cleanup(lease, **kwargs):
        assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
        # The injected preparation clock has fired. Cleanup owns a fresh budget.
        monkeypatch.setattr(worker, "time", time)
        return original(lease, **kwargs)
    monkeypatch.setattr(plugin_state_io, "discard_supervised", cleanup)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        sandbox._save_state(packet(), root, plugin="owner", caller="platform")
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert (root / "value").read_bytes() == b"old"
    assert not list(root.parent.glob(".state-*"))
    assert not root.with_name("self.transaction.json").exists()


def test_success_does_not_extract_or_prepare_permissions_in_parent(tmp_path, monkeypatch):
    from core import archive, secrets
    root = tmp_path.resolve() / "self"
    root.mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail("candidate I/O ran in parent")
    monkeypatch.setattr(archive, "extract_tar", forbidden)
    # Scope directly to candidate preparation: config/trust persistence also
    # legitimately uses restrict_permissions in the parent during publication.
    monkeypatch.setattr(secrets, "restrict_permissions", forbidden)
    with sandbox._state_staging(root, packet(), plugin="owner", caller="platform") as lease:
        candidate = lease.candidate
        assert (candidate / "value").read_bytes() == b"new"


def test_unconfirmed_writer_exit_retains_candidate(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    def uncertain(payload, root, *, on_allocate, **kwargs):
        from core.plugin_state_io import OwnedStaging
        from core.plugin_state import _directory
        wrapper = root.parent / ".state-owned"
        staging = wrapper / "new"
        staging.mkdir(parents=True)
        (staging / "partial").write_bytes(b"retain")
        on_allocate(OwnedStaging(root, wrapper, _directory(root.parent), _directory(wrapper)))
        raise preparation.PreparationCleanupError("injected unconfirmed exit")
    monkeypatch.setattr(preparation, "prepare_candidate", uncertain)
    with pytest.raises(preparation.PreparationCleanupError):
        sandbox._save_state(packet(), root, plugin="owner", caller="platform")
    wrappers = list(root.parent.glob(".state-*"))
    assert len(wrappers) == 1
    assert (wrappers[0] / "new" / "partial").read_bytes() == b"retain"
    assert not root.with_name("self.transaction.json").exists()


@pytest.mark.parametrize("payload", ["é", "%%%", ""])
def test_invalid_payload_cannot_publish(tmp_path, payload):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    with pytest.raises((ValueError, RuntimeError)):
        sandbox._save_state(payload, root, plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"old"
    assert not list(root.parent.glob(".state-*"))


@pytest.mark.parametrize("extra", [0, 1])
def test_payload_budget_boundary_crosses_shared_transport(tmp_path, extra):
    output = io.BytesIO()
    size = sandbox.STATE_LIMIT + extra
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("value")
        member.size = size
        archive.addfile(member, io.BytesIO(b"x" * size))
    payload = base64.b64encode(output.getvalue()).decode("ascii")
    root = tmp_path.resolve() / "self"
    root.mkdir()
    if extra:
        with pytest.raises(ValueError):
            with sandbox._state_staging(root, payload, plugin="owner", caller="platform"):
                pytest.fail("oversized candidate accepted")
    else:
        with sandbox._state_staging(root, payload, plugin="owner", caller="platform") as lease:
            candidate = lease.candidate
            assert (candidate / "value").stat().st_size == sandbox.STATE_LIMIT
            with (candidate / "value").open("rb") as stream:
                assert stream.read(1) == b"x"
                stream.seek(-1, 2)
                assert stream.read() == b"x"
