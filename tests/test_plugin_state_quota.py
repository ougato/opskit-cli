"""Unjournaled staging can consume a bounded allowance, never name-only cleanup."""
import os
from pathlib import Path

import pytest

from core import plugin_sandbox as sandbox, plugin_state as state
from state_publication_fixture import state_packet


@pytest.fixture
def root(tmp_path):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    return root


def orphan(root, name, size=3):
    wrapper = root.parent / (".state-" + name)
    wrapper.mkdir()
    with (wrapper / "value").open("wb") as stream:
        stream.truncate(size)
    return wrapper


def test_one_slot_and_payload_are_reserved_without_removing_unknowns(root):
    wrappers = [orphan(root, str(index)) for index in range(state.MAX_STAGING_DIRECTORIES - 1)]
    assert state.reserve_staging(root) == (7, 21)
    with sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform") as lease:
        assert lease.candidate.parent.exists()
        with pytest.raises(state.StateTransactionError, match="quota exhausted"):
            state.reserve_staging(root)
    assert all((wrapper / "value").is_file() for wrapper in wrappers)
    assert (root / "value").read_bytes() == b"old"


def test_directory_quota_refuses_before_payload_decode_or_temp_creation(root, monkeypatch):
    wrappers = [orphan(root, str(index)) for index in range(state.MAX_STAGING_DIRECTORIES)]
    def forbidden(*args, **kwargs):
        pytest.fail("quota refusal reached allocation or decoding")
    import tempfile
    monkeypatch.setattr(tempfile, "mkdtemp", forbidden)
    with pytest.raises(sandbox.SandboxError, match="quota check failed"):
        sandbox._save_state("", root, plugin="provider", caller="platform")
    assert all((wrapper / "value").is_file() for wrapper in wrappers)


@pytest.mark.parametrize("extra", [0, 1])
def test_logical_byte_quota_reserves_one_candidate(root, extra):
    wrapper = orphan(root, "large", state.MAX_STAGING_BYTES - state.STAGING_RESERVATION_BYTES + extra)
    if extra:
        with pytest.raises(state.StateTransactionError, match="byte quota exhausted"):
            state.reserve_staging(root)
    else:
        assert state.reserve_staging(root) == (1, state.MAX_STAGING_BYTES - state.STAGING_RESERVATION_BYTES)
    assert (wrapper / "value").stat().st_size == state.MAX_STAGING_BYTES - state.STAGING_RESERVATION_BYTES + extra


def test_partition_scan_counts_unrelated_entries_but_does_not_recurse(root, monkeypatch):
    monkeypatch.setattr(state, "MAX_PARTITION_ENTRIES", 2)
    other = root.parent / "unrelated"
    other.mkdir()
    (other / "nested").mkdir()
    assert state.reserve_staging(root) == (0, 0)
    (root.parent / "third").touch()
    with pytest.raises(state.StateTransactionError, match="partition inventory"):
        state.reserve_staging(root)
    assert (other / "nested").is_dir()


def test_staging_inventory_has_one_shared_node_allowance(root, monkeypatch):
    monkeypatch.setattr(state, "MAX_STAGING_ENTRIES", 2)
    wrappers = [orphan(root, str(index)) for index in range(3)]
    with pytest.raises(state.StateTransactionError, match="inventory exceeds limit"):
        state.reserve_staging(root)
    assert all((wrapper / "value").is_file() for wrapper in wrappers)


@pytest.mark.parametrize("linked_root", [False, True])
def test_unknown_staging_links_are_never_followed_or_removed(root, linked_root):
    foreign = root.parent / "foreign"
    foreign.mkdir()
    (foreign / "keep").write_bytes(b"keep")
    wrapper = root.parent / ".state-unknown"
    try:
        if linked_root:
            wrapper.symlink_to(foreign, target_is_directory=True)
        else:
            wrapper.mkdir()
            (wrapper / "link").symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError):
        state.reserve_staging(root)
    assert (foreign / "keep").read_bytes() == b"keep"
    assert os.path.lexists(wrapper)


def test_checkpoint_cancel_preserves_all_unknown_staging(root):
    wrapper = orphan(root, "retained")
    def cancel():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        state.reserve_staging(root, checkpoint=cancel)
    assert (wrapper / "value").is_file()


def prejournal_crashing_worker(connection, *args):
    from core import plugin_state_prepare
    original = Path.mkdir
    def mkdir(path, *arguments, **kwargs):
        original(path, *arguments, **kwargs)
        if path.name.startswith(".state-"):
            (path / "value").write_bytes(b"crash")
            os._exit(73)
    Path.mkdir = mkdir
    plugin_state_prepare._prepare_worker(connection, *args)


def test_real_prejournal_crash_is_charged_and_blocks_next_allocation(root, monkeypatch):
    wrappers = [orphan(root, str(index)) for index in range(state.MAX_STAGING_DIRECTORIES - 1)]
    with monkeypatch.context() as patch:
        patch.setattr("core.plugin_state_prepare._prepare_worker", prejournal_crashing_worker)
        with pytest.raises(RuntimeError, match="allocation worker failed"):
            with sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform"):
                pytest.fail("crashed allocation was accepted")
    assert not state.record_path(root).exists()
    assert len(list(root.parent.glob(".state-*"))) == state.MAX_STAGING_DIRECTORIES
    with pytest.raises(sandbox.SandboxError, match="quota check failed"):
        with sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform"):
            pytest.fail("full retained inventory admitted another staging directory")
    assert all((wrapper / "value").is_file() for wrapper in wrappers)
    assert (root / "value").read_bytes() == b"old"
