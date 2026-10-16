"""Discovery never implies ownership or permission to retire a token."""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest
from typer.testing import CliRunner

from core import plugin_resource_io as io, plugin_resources as resources
from core.cli import configure
from core.resource_lock import ResourceLock
from worker_test_markers import publish_marker


def snapshot(root):
    return {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mode, p.stat().st_mtime_ns)
            for p in root.iterdir() if p.is_file()}


def test_inspection_of_missing_root_creates_nothing(tmp_path, monkeypatch):
    home = tmp_path / 'not-created'
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(home))
    assert io.inspect_lock_metadata(timeout=30) == {'entries': [], 'unknown': 0}
    assert not home.exists()


def test_real_active_lock_journal_evidence_and_unknown_files_are_unchanged():
    root = resources._root()
    orphan, recorded, only_journal, unsafe = ('a' * 32, 'b' * 32, 'c' * 32, 'd' * 32)
    (root / (orphan + '.lock')).write_bytes(b'active metadata')
    (root / (recorded + '.lock')).write_bytes(b'recorded metadata')
    (root / (recorded + '.json')).write_bytes(b'corrupt evidence with private text')
    (root / (only_journal + '.json')).mkdir()
    foreign = root / 'foreign-private-name'
    foreign.write_bytes(b'foreign')
    os.link(foreign, root / (unsafe + '.lock'))
    before = snapshot(root)
    with ResourceLock(lambda: root / (orphan + '.lock'), label='active').hold():
        actual = io.inspect_lock_metadata(timeout=30)
    assert snapshot(root) == before
    assert actual == {'entries': [
        {'token': orphan, 'journal': False, 'lock': 'present'},
        {'token': recorded, 'journal': True, 'lock': 'present'},
        {'token': only_journal, 'journal': True, 'lock': 'absent'},
        {'token': unsafe, 'journal': False, 'lock': 'unsafe'}], 'unknown': 1}
    assert 'private' not in json.dumps(actual)
    assert not (root / '.index.lock').exists()


def test_token_limit_rejects_whole_inventory_without_partial_success():
    root = resources._root()
    for token in ('a' * 32, 'b' * 32):
        (root / (token + '.lock')).touch()
        (root / (token + '.json')).touch()
    before = snapshot(root)
    with pytest.raises(RuntimeError, match='exceeds inspection limit'):
        io.inspect_lock_metadata(timeout=30, limit=1)
    assert snapshot(root) == before
    assert len(io.inspect_lock_metadata(timeout=30, limit=2)['entries']) == 2


def limited_worker(connection, *args):
    io.MAX_INVENTORY_ENTRIES = 3
    io._metadata_worker(connection, *args)


def test_unknown_entries_also_have_a_scan_budget(monkeypatch):
    root = resources._root()
    for index in range(4):
        (root / str(index)).touch()
    monkeypatch.setattr(io, '_metadata_worker', limited_worker)
    with pytest.raises(RuntimeError, match='exceeds inspection limit'):
        io.inspect_lock_metadata(timeout=30)
    assert len(list(root.iterdir())) == 4


def replacing_worker(connection, *args):
    from core import plugin_state
    original = plugin_state._directory
    calls = 0
    def directory(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.rename(path.with_name('retained-evidence'))
            path.mkdir()
        return original(path)
    plugin_state._directory = directory
    io._metadata_worker(connection, *args)


def test_root_replacement_is_not_a_confirmed_inventory(monkeypatch):
    root = resources._root()
    (root / ('a' * 32 + '.lock')).write_bytes(b'keep')
    monkeypatch.setattr(io, '_metadata_worker', replacing_worker)
    with pytest.raises(RuntimeError, match='changed during inspection'):
        io.inspect_lock_metadata(timeout=30)
    assert (root.with_name('retained-evidence') / ('a' * 32 + '.lock')).read_bytes() == b'keep'


def denied_journal_worker(connection, *args):
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path.name == 'a' * 32 + '.json':
            raise PermissionError('private journal access denied')
        return original(path, *args, **kwargs)
    Path.lstat = lstat
    io._metadata_worker(connection, *args)


def test_unreadable_journal_metadata_is_not_reported_as_absent(monkeypatch):
    root = resources._root()
    (root / ('a' * 32 + '.lock')).write_bytes(b'keep')
    (root / ('a' * 32 + '.json')).write_bytes(b'evidence')
    before = snapshot(root)
    monkeypatch.setattr(io, '_metadata_worker', denied_journal_worker)
    with pytest.raises(RuntimeError, match='inspection failed'):
        io.inspect_lock_metadata(timeout=30)
    assert snapshot(root) == before


def blocked_worker(connection, *args):
    from network import worker
    stage = os.environ['OPSKIT_METADATA_STAGE']
    def block():
        read, write = os.pipe()
        publish_marker(os.environ['OPSKIT_METADATA_MARKER'], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    if stage == 'scan':
        os.scandir = lambda *args: block()
    elif stage in ('stat', 'journal'):
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path.name == 'a' * 32 + ('.json' if stage == 'journal' else '.lock'):
                block()
            return original(path, *args, **kwargs)
        Path.lstat = lstat
    else:
        worker.send = lambda *args: block()
    io._metadata_worker(connection, *args)


@pytest.mark.parametrize('stage', ['scan', 'stat', 'journal', 'response'])
@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_inspection_is_reaped_and_leaves_files_unchanged(tmp_path, monkeypatch,
                                                               expire_worker_after_marker, stage, cancel):
    from network import worker
    root = resources._root()
    (root / ('a' * 32 + '.lock')).write_bytes(b'keep')
    before = snapshot(root)
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_METADATA_STAGE', stage)
    monkeypatch.setenv('OPSKIT_METADATA_MARKER', str(marker))
    monkeypatch.setattr(io, '_metadata_worker', blocked_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        io.inspect_lock_metadata(timeout=30)
    assert not psutil.pid_exists(int(marker.read_text()))
    assert snapshot(root) == before


@pytest.mark.parametrize('change', [
    {'ok': 1}, {'extra': True}, {'request': 'stale'}, {'unknown': True}, {'unknown': 4097},
    {'unknown': 4096, 'entries': [{'token': 'a' * 32, 'journal': False, 'lock': 'present'}]},
    {'entries': [{'token': '../private', 'journal': False, 'lock': 'present'}]},
    {'entries': [{'token': 'a' * 32, 'journal': 1, 'lock': 'present'}]},
    {'entries': [{'token': 'a' * 32, 'journal': False, 'lock': 'active'}]},
    {'entries': [{'token': 'a' * 32, 'journal': False, 'lock': 'present'}] * 2},
])
def test_untrusted_worker_results_are_not_exposed(monkeypatch, change):
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda target, args, **kwargs:
                        {'ok': True, 'request': args[0], 'entries': [], 'unknown': 0, **change})
    with pytest.raises(RuntimeError):
        io.inspect_lock_metadata(timeout=30)


@pytest.mark.parametrize('limit', [0, 257, True, 1.5])
def test_invalid_limit_does_not_start_a_worker(monkeypatch, limit):
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: pytest.fail('invalid capacity started worker'))
    with pytest.raises(ValueError, match='capacity'):
        io.inspect_lock_metadata(timeout=30, limit=limit)


def test_cli_json_is_read_only_and_needs_no_docker(tmp_path, monkeypatch):
    import main
    from core import plugin_sandbox
    monkeypatch.setattr(plugin_sandbox, 'docker_command', lambda: pytest.fail('inspection needs no Docker'))
    root = resources._root()
    (root / ('a' * 32 + '.lock')).write_bytes(b'keep')
    before = snapshot(root)
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'inspect-locks', '--limit', '1'])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)['data'] == {'entries': [{'token': 'a' * 32, 'journal': False, 'lock': 'present'}], 'unknown': 0}
        assert snapshot(root) == before
    finally:
        configure('text', False)


@pytest.mark.parametrize('limit', ['0', '257'])
def test_cli_invalid_limits_are_usage_errors(limit):
    import main
    try:
        result = CliRunner().invoke(main.app, ['plugin', 'inspect-locks', '--limit', limit])
        assert result.exit_code == 2
    finally:
        configure('text', False)


@pytest.mark.parametrize('error, code', [(OSError('/private/path'), 1), (KeyboardInterrupt(), 130)])
def test_cli_operational_failures_are_sanitized(monkeypatch, error, code):
    import main
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(io, 'inspect_lock_metadata', fail)
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'inspect-locks'])
        assert result.exit_code == code, result.output
        assert '/private' not in result.output
        if code == 1:
            assert json.loads(result.output)['error']['code'] == 1
    finally:
        configure('text', False)
