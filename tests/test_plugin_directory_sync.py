"""Directory barriers must reject changed identities and close failed handles."""
import errno
import os
import pytest
from core import plugin_metadata as metadata
from core.plugin_state import _directory

@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory fsync contract')
def test_failed_fsync_closes_real_directory_handle(tmp_path, monkeypatch):
    observed = []
    def failed(descriptor):
        observed.append(descriptor)
        raise OSError(errno.EIO, 'injected directory sync failure')
    monkeypatch.setattr(os, 'fsync', failed)
    with pytest.raises(OSError) as failure:
        metadata.sync_directory(tmp_path, _directory(tmp_path))
    assert failure.value.errno == errno.EIO and len(observed) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(observed[0])
    assert closed.value.errno == errno.EBADF


def test_sync_rejects_wrong_identity_before_open(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'open', lambda *a, **k: pytest.fail('opened wrong directory'))
    identity = _directory(tmp_path)
    with pytest.raises(ValueError, match='before sync'):
        metadata.sync_directory(tmp_path, [identity[0], identity[1] + 1])


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory fsync contract')
def test_directory_replaced_during_fsync_is_not_confirmed(tmp_path, monkeypatch):
    target = tmp_path / 'directory'
    target.mkdir()
    original_identity = _directory(target)
    old = tmp_path / 'original'
    original = os.fsync
    def changed(descriptor):
        original(descriptor)
        target.rename(old)
        target.mkdir()
    monkeypatch.setattr(os, 'fsync', changed)
    with pytest.raises(ValueError, match='during sync'):
        metadata.sync_directory(target, original_identity)
    assert _directory(old) == original_identity
    assert _directory(target) != original_identity
