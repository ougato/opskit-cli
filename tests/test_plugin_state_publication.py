"""Publication I/O and its fresh authorization belong to one supervised worker."""
import json
import os
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_publish as publication
from core.plugin_state_io import OwnedStaging
from state_publication_fixture import publication_worker, state_packet


@pytest.fixture
def candidate(tmp_path):
    from core.config import get_config_path
    from core.plugin_trust import grant, _trust_file
    grant("owner", "a" * 64, "1")
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    wrapper = root.parent / ".state-owned"
    (wrapper / "new").mkdir(parents=True)
    (wrapper / "new" / "value").write_bytes(b"new")
    lease = OwnedStaging(root, wrapper, state._directory(root.parent), state._directory(wrapper))
    state.prepare_record(root, lease.candidate, plugin="owner", caller="platform")
    requirements = {"operation": "authorize", "config": str(get_config_path()), "trust": str(_trust_file()),
                    "identities": (("owner", "a" * 64),), "edges": (), "networks": ()}
    return lease, state._directory(lease.candidate), requirements


def publish(candidate, **kwargs):
    lease, identity, requirements = candidate
    publication.publish_supervised(lease, identity, plugin="owner", caller="platform",
                                   requirements=requirements, timeout=30, **kwargs)


@pytest.mark.parametrize("stage", ["read", "sync", "old", "new"])
@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_publication_reaps_worker_and_recovers_expected_state(candidate, tmp_path, monkeypatch,
                                                                    expire_worker_after_marker, stage, cancel):
    from network import worker
    lease, identity, requirements = candidate
    marker = tmp_path / "publication-pid"
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MODE", "block_" + stage)
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MARKER", str(marker))
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_publish_worker", publication_worker)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
        else:
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError,
                           match=None if cancel else "publication; outcome unconfirmed"):
            publish(candidate)
    monkeypatch.setattr(worker, "time", time)
    process = json.loads(marker.read_text())
    assert not psutil.pid_exists(process["pid"])
    from core.config import configuration_transaction
    from core.plugin_trust import authorization_transaction
    with configuration_transaction(), authorization_transaction(), state.transaction_guard(lease.root):
        pass
    if stage == "read":
        assert state.record_path(lease.root).exists()
        assert (lease.root / "value").read_bytes() == b"old"
    sandbox._recover_state(lease.root, plugin="owner", caller="platform")
    expected = b"new" if stage == "new" else b"old"
    assert (lease.root / "value").read_bytes() == expected
    assert not lease.wrapper.exists()
    assert not state.record_path(lease.root).exists()


def malformed_publication_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        result.update(json.loads(os.environ["OPSKIT_TEST_PUBLICATION_RESULT"]))
        original(connection, result)
    worker.send = send
    publication._publish_worker(connection, *args)


@pytest.mark.parametrize("change", [{"request": "stale"}, {"root": "/other"}, {"candidate": "/other"},
                                    {"ok": 1}, {"outcome": "aborted"}, {"extra": True}])
def test_invalid_publication_response_is_uncertain_and_retains_journal(candidate, monkeypatch, change):
    lease, _, _ = candidate
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_RESULT", json.dumps(change))
    monkeypatch.setattr(publication, "_publish_worker", malformed_publication_worker)
    with pytest.raises(publication.PublicationUncertainError, match="outcome unconfirmed"):
        publish(candidate)
    assert (lease.root / "value").read_bytes() == b"new"
    assert state.record_path(lease.root).exists()
    sandbox._recover_state(lease.root, plugin="owner", caller="platform")
    assert (lease.root / "value").read_bytes() == b"new"
    assert not lease.wrapper.exists()


@pytest.mark.parametrize("change", ["missing_owner", "wrong_path", "candidate_identity", "wrapper_identity"])
def test_unbound_publication_preserves_preparation_journal(candidate, change):
    lease, identity, requirements = candidate
    journal_before = state.record_path(lease.root).read_bytes()
    if change == "missing_owner":
        requirements["identities"] = ()
    elif change == "wrong_path":
        requirements["trust"] = str(lease.root / "foreign-trust")
    elif change == "candidate_identity":
        identity[:] = [0, 1]
    else:
        lease.wrapper_identity = [0, 1]
    with pytest.raises((sandbox.SandboxError, publication.PublicationUncertainError)):
        publish(candidate)
    assert (lease.root / "value").read_bytes() == b"old"
    assert (lease.candidate / "value").read_bytes() == b"new"
    assert state.record_path(lease.root).read_bytes() == journal_before


def test_fresh_read_refuses_revocation_after_parent_requirements_were_built(candidate):
    from core.plugin_trust import revoke
    lease, _, _ = candidate
    journal_before = state.record_path(lease.root).read_bytes()
    revoke("owner")
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        publish(candidate)
    assert state.record_path(lease.root).read_bytes() == journal_before
    assert (lease.root / "value").read_bytes() == b"old"


def test_parent_does_not_read_authorization_or_publish_files(candidate, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("publication I/O ran in supervisor")
    monkeypatch.setattr(sandbox, "_read_live_authorizations", forbidden)
    monkeypatch.setattr(sandbox, "_publish_state_locked", forbidden)
    monkeypatch.setattr(state, "transaction_guard", forbidden)
    publish(candidate)
    lease, _, _ = candidate
    assert (lease.root / "value").read_bytes() == b"new"


def test_unconfirmed_worker_exit_retains_invocation_staging(tmp_path, monkeypatch):
    from network import worker
    root = tmp_path.resolve() / "self"
    root.mkdir()
    def uncertain(*args, **kwargs):
        raise RuntimeError("worker cleanup failed")
    monkeypatch.setattr(worker, "run", uncertain)
    from core.config import get_config_path
    from core.plugin_trust import _trust_file
    request = {"operation": "authorize", "config": str(get_config_path()), "trust": str(_trust_file()),
               "identities": (("owner", "a" * 64),), "edges": ()}
    with pytest.raises(publication.PublicationUncertainError):
        with sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform") as lease:
            publication.publish_supervised(lease, state._directory(lease.candidate), plugin="owner", caller="platform",
                                           requirements=request, timeout=30)
    assert lease.candidate.exists()


def test_caller_must_be_bound_in_publication_requirements(candidate):
    lease, identity, requirements = candidate
    journal_before = state.record_path(lease.root).read_bytes()
    with pytest.raises(sandbox.SandboxError, match="authorization requirements"):
        publication.publish_supervised(lease, identity, plugin="owner", caller="other",
                                       requirements=requirements, timeout=30)
    assert state.record_path(lease.root).read_bytes() == journal_before


def test_private_grant_is_rechecked_from_disk(candidate):
    from core.config import save_config
    lease, _, requirements = candidate
    journal_before = state.record_path(lease.root).read_bytes()
    targets = ["10.0.0.1:8765"]
    save_config({"plugin": {"sandbox": {"private_targets": {"owner": targets}}}})
    requirements["networks"] = (("owner", tuple(targets)),)
    save_config({"plugin": {"sandbox": {"private_targets": {"owner": []}}}})
    with pytest.raises(sandbox.SandboxError, match="private network authorization revoked"):
        publish(candidate)
    assert state.record_path(lease.root).read_bytes() == journal_before


def test_service_edge_is_rechecked_from_disk(candidate):
    from core.plugin_trust import grant, grant_service, revoke_service
    lease, _, requirements = candidate
    journal_before = state.record_path(lease.root).read_bytes()
    grant("caller", "b" * 64, "1")
    grant_service("caller", "b" * 64, "storage", "owner", "a" * 64, 7, ["read"])
    requirements["identities"] = (*requirements["identities"], ("caller", "b" * 64))
    requirements["edges"] = (("caller", "b" * 64, "storage", "owner", "a" * 64, 7, "read"),)
    revoke_service("caller", "storage")
    with pytest.raises(sandbox.SandboxError, match="service authorization revoked"):
        publish(candidate)
    assert state.record_path(lease.root).read_bytes() == journal_before
