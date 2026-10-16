"""Metadata errors must not become permission to overwrite or retire evidence."""
import os
from pathlib import Path

import pytest

from core import plugin_resource_io as io, plugin_resources as resources
from test_plugin_resource_creation import value


def deny_journal_metadata():
    original_path_lstat = Path.lstat
    def path_lstat(path, *args, **kwargs):
        if path.name == 'a' * 32 + '.json':
            raise PermissionError('injected journal metadata access denial')
        return original_path_lstat(path, *args, **kwargs)
    # Python 3.10 pathlib caches OS accessors before os.stat/lstat are patched.
    Path.lstat = path_lstat
    original = os.lstat
    original_stat = os.stat
    def lstat(path, *args, **kwargs):
        if Path(path).name == 'a' * 32 + '.json':
            raise PermissionError('injected journal metadata access denial')
        return original(path, *args, **kwargs)
    os.lstat = lstat
    # pathlib may implement lstat using stat(follow_symlinks=False).
    def stat(path, *args, **kwargs):
        if (isinstance(path, (str, os.PathLike)) and Path(path).name == 'a' * 32 + '.json'
                and kwargs.get('follow_symlinks') is False):
            raise PermissionError('injected journal metadata access denial')
        return original_stat(path, *args, **kwargs)
    os.stat = stat


def denied_retirement_worker(connection, *args):
    deny_journal_metadata()
    io._retire_worker(connection, *args)


def denied_creation_worker(connection, *args):
    deny_journal_metadata()
    io._create_worker(connection, *args)


@pytest.mark.parametrize('operation', ['retire', 'create'])
def test_denied_journal_metadata_preserves_original_evidence(monkeypatch, operation):
    root = resources._root()
    journal = root / ('a' * 32 + '.json')
    journal.write_bytes(b'original damaged journal evidence')
    lock = journal.with_suffix('.lock')
    lock.write_bytes(b'original lock metadata')
    error = None
    try:
        if operation == 'retire':
            monkeypatch.setattr(io, '_retire_worker', denied_retirement_worker)
            io.retire_orphan_lock('a' * 32, timeout=30)
        else:
            monkeypatch.setattr(io, '_create_worker', denied_creation_worker)
            io.create_record(journal, value(), timeout=30, limit=256)
    except OSError as caught:
        error = caught
    assert journal.read_bytes() == b'original damaged journal evidence'
    assert lock.read_bytes() == b'original lock metadata'
    assert isinstance(error, OSError), 'metadata denial must be an operational failure'
