"""Denied state metadata is unknown, never an absent transaction or backup."""
import os
from pathlib import Path

import pytest

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_io as transaction_io
from test_plugin_state_discard import owned
from test_plugin_state_recovery import transaction


def deny_metadata(path, monkeypatch=None):
    target = os.path.normcase(str(path))
    original_lstat, original_stat = os.lstat, os.stat
    original_path_lstat = Path.lstat
    def selected(path):
        return isinstance(path, (str, os.PathLike)) and os.path.normcase(str(path)) == target
    def lstat(path, *args, **kwargs):
        if selected(path):
            raise PermissionError('injected state metadata access denial')
        return original_lstat(path, *args, **kwargs)
    def stat(path, *args, **kwargs):
        if selected(path) and kwargs.get('follow_symlinks') is False:
            raise PermissionError('injected state metadata access denial')
        return original_stat(path, *args, **kwargs)
    def path_lstat(path, *args, **kwargs):
        if selected(path):
            raise PermissionError('injected state metadata access denial')
        return original_path_lstat(path, *args, **kwargs)
    if monkeypatch is None:
        os.lstat, os.stat = lstat, stat
        Path.lstat = path_lstat
    else:
        monkeypatch.setattr(os, 'lstat', lstat)
        monkeypatch.setattr(os, 'stat', stat)
        monkeypatch.setattr(Path, 'lstat', path_lstat)


@pytest.mark.parametrize('part', ['record', 'previous'])
def test_resolution_preflight_does_not_accept_denied_metadata(tmp_path, monkeypatch, part):
    root = tmp_path.resolve() / 'self'
    path = state.record_path(root) if part == 'record' else root.with_name('self.previous')
    with monkeypatch.context() as patch:
        deny_metadata(path, patch)
        with pytest.raises(PermissionError):
            sandbox._require_resolved_state(root)
    assert not state.record_path(root).exists()


@pytest.mark.parametrize('part', ['root', 'previous'])
def test_preparation_does_not_create_record_after_metadata_denial(tmp_path, monkeypatch, part):
    lease = owned(tmp_path)
    path = lease.root if part == 'root' else lease.root.with_name('self.previous')
    with monkeypatch.context() as patch:
        deny_metadata(path, patch)
        with pytest.raises(PermissionError):
            state.prepare_record(lease.root, lease.candidate, plugin='owner', caller='platform')
    assert not state.record_path(lease.root).exists()
    assert (lease.root / 'value').read_bytes() == b'old'
    assert (lease.candidate / 'value').read_bytes() == b'new'


@pytest.mark.parametrize('part', ['record', 'previous'])
def test_finish_does_not_report_no_transaction_on_metadata_denial(tmp_path, monkeypatch, part):
    root = tmp_path.resolve() / 'self'
    if part == 'record':
        root, _ = transaction(tmp_path)
        path = state.record_path(root)
    else:
        path = root.with_name('self.previous')
        path.mkdir()
        (path / 'value').write_bytes(b'legacy evidence')
    with monkeypatch.context() as patch:
        deny_metadata(path, patch)
        with pytest.raises(PermissionError):
            state.finish_transaction(root, plugin='provider', caller='platform')
    assert path.exists()


def denied_finish_worker(connection, *args):
    deny_metadata(os.environ['OPSKIT_TEST_STATE_DENIED_PATH'])
    transaction_io._finish_worker(connection, *args)


def test_recovery_worker_refuses_denial_without_false_success(tmp_path, monkeypatch):
    root, candidate = transaction(tmp_path)
    original = state.record_path(root).read_bytes()
    monkeypatch.setenv('OPSKIT_TEST_STATE_DENIED_PATH', str(state.record_path(root)))
    monkeypatch.setattr(transaction_io, '_finish_worker', denied_finish_worker)
    with pytest.raises(state.StateTransactionError, match='recovery refused'):
        transaction_io.finish_supervised(root, plugin='provider', caller='platform', timeout=30)
    assert state.record_path(root).read_bytes() == original
    assert (root / 'value').read_bytes() == b'old'
    assert (candidate / 'value').read_bytes() == b'new'


def denied_discard_worker(connection, *args):
    deny_metadata(os.environ['OPSKIT_TEST_STATE_DENIED_PATH'])
    transaction_io._discard_worker(connection, *args)


@pytest.mark.parametrize('bound', [False, True])
def test_discard_preserves_journaled_candidate_when_metadata_is_denied(tmp_path, monkeypatch, bound):
    lease = owned(tmp_path)
    state.prepare_record(lease.root, lease.candidate, plugin='owner', caller='platform')
    journal = state.record_path(lease.root)
    original = journal.read_bytes()
    monkeypatch.setenv('OPSKIT_TEST_STATE_DENIED_PATH', str(journal))
    monkeypatch.setattr(transaction_io, '_discard_worker', denied_discard_worker)
    binding = {'plugin': 'owner', 'caller': 'platform'} if bound else {}
    with pytest.raises(RuntimeError, match='cleanup refused'):
        transaction_io.discard_supervised(lease, timeout=30, **binding)
    assert journal.read_bytes() == original
    assert (lease.root / 'value').read_bytes() == b'old'
    assert (lease.candidate / 'value').read_bytes() == b'new'
