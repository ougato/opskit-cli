"""Ownership is recoverable even when allocation never reaches its supervisor."""
import json
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_prepare as preparation
from state_publication_fixture import state_packet
from test_plugin_state_allocation import allocation_worker


@pytest.mark.parametrize('stage', ['before_ack', 'record_sync'])
@pytest.mark.parametrize('cancel', [False, True])
def test_unacknowledged_preparation_has_recoverable_journal(tmp_path, monkeypatch,
                                                           expire_worker_after_marker, stage, cancel):
    from network import worker
    root = tmp_path.resolve() / 'self'
    root.mkdir()
    (root / 'value').write_bytes(b'committed-old')
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_ALLOCATION_STAGE', stage)
    monkeypatch.setenv('OPSKIT_TEST_ALLOCATION_MARKER', str(marker))
    monkeypatch.setattr(preparation, '_prepare_worker', allocation_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        sandbox._save_state(state_packet(), root, plugin='owner', caller='platform')
    monkeypatch.setattr(worker, 'time', time)
    identity = json.loads(marker.read_text())
    assert not psutil.pid_exists(identity['pid'])
    record = state.read_record(root, plugin='owner', caller='platform')
    wrapper = root.parent / record.staging
    assert tuple(state._directory(wrapper / 'new')) == record.new_identity
    assert not list((wrapper / 'new').iterdir()), 'no candidate bytes before acknowledged ownership'
    assert (root / 'value').read_bytes() == b'committed-old'
    sandbox._recover_state(root, plugin='owner', caller='platform')
    assert (root / 'value').read_bytes() == b'committed-old'
    assert not wrapper.exists() and not state.record_path(root).exists()


def test_identical_journal_replaced_during_sync_is_not_confirmation(tmp_path, monkeypatch):
    import os
    root = tmp_path.resolve() / 'self'
    root.mkdir()
    (root / 'value').write_bytes(b'old')
    candidate = root.parent / '.state-owned' / 'new'
    candidate.mkdir(parents=True)
    (candidate / 'value').write_bytes(b'new')
    state.prepare_record(root, candidate, plugin='owner', caller='platform')
    path = state.record_path(root)
    raw = path.read_bytes()
    original = os.fsync
    def sync(descriptor):
        original(descriptor)
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(raw)
        os.replace(replacement, path)
    monkeypatch.setattr(state.os, 'fsync', sync)
    # Windows denies replacing the still-open record; POSIX permits rename
    # and therefore needs the explicit post-fsync identity check.
    with pytest.raises(PermissionError if os.name == 'nt' else state.StateTransactionError):
        state.confirm_prepared_record(root, candidate, plugin='owner', caller='platform')
    assert path.read_bytes() == raw
    assert (root / 'value').read_bytes() == b'old'
    assert (candidate / 'value').read_bytes() == b'new'
