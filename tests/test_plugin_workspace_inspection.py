"""Workspace discovery is bounded observation, never ownership or cleanup."""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest
from typer.testing import CliRunner

from core import plugin_workspace as workspace
from core.cli import configure
from test_plugin_workspace import block


def namespace(tmp_path):
    parent = tmp_path / 'plugin-runtimes' / 'workspaces'
    parent.mkdir(parents=True)
    return parent


def test_missing_namespace_is_not_created(tmp_path):
    assert workspace.inspect_workspaces(timeout=30) == {'entries': [], 'unknown': 0}
    assert not (tmp_path / 'plugin-runtimes').exists()


def test_inventory_preserves_unknown_entries_and_never_reads_contents(tmp_path):
    parent = namespace(tmp_path)
    directory = parent / ('opskit-sandbox-' + 'b' * 32)
    directory.mkdir()
    (directory / 'private').write_bytes(b'not an ownership record')
    unsafe = parent / ('opskit-sandbox-' + 'a' * 32)
    unsafe.write_bytes(b'not a directory')
    (parent / 'private-name-secret').write_bytes(b'unknown')
    assert workspace.inspect_workspaces(timeout=30) == {'entries': [
        {'token': 'a' * 32, 'kind': 'unsafe'},
        {'token': 'b' * 32, 'kind': 'directory'}], 'unknown': 1}
    assert (directory / 'private').read_bytes() == b'not an ownership record'
    assert unsafe.read_bytes() == b'not a directory'
    assert (parent / 'private-name-secret').read_bytes() == b'unknown'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['plugin-runtimes']


def test_entry_limit_rejects_partial_success(tmp_path):
    parent = namespace(tmp_path)
    for token in ('a' * 32, 'b' * 32):
        (parent / ('opskit-sandbox-' + token)).mkdir()
    with pytest.raises(RuntimeError, match='limit'):
        workspace.inspect_workspaces(timeout=30, limit=1)
    assert len(list(parent.iterdir())) == 2


def test_unknown_entries_are_also_scan_bounded(tmp_path):
    parent = namespace(tmp_path)
    for index in range(4097):
        (parent / str(index)).touch()
    with pytest.raises(RuntimeError, match='limit'):
        workspace.inspect_workspaces(timeout=30)
    assert len(list(parent.iterdir())) == 4097


@pytest.mark.parametrize('limit', [True, 0, 257, 1.5, '1'])
def test_invalid_limit_never_starts_worker(monkeypatch, limit):
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: pytest.fail('invalid limit started worker'))
    with pytest.raises(ValueError, match='capacity'):
        workspace.inspect_workspaces(timeout=30, limit=limit)


@pytest.mark.parametrize('timeout', [True, 0, -1, float('nan'), float('inf'), '1'])
def test_invalid_allowance_never_starts_worker(monkeypatch, timeout):
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: pytest.fail('invalid timeout started worker'))
    with pytest.raises(ValueError, match='allowance'):
        workspace.inspect_workspaces(timeout=timeout)


@pytest.mark.parametrize('response', [
    {}, {'ok': True, 'entries': [], 'unknown': 0},
    {'entries': [], 'unknown': True}, {'entries': [], 'unknown': -1},
    {'entries': [], 'unknown': 4097}, {'entries': None, 'unknown': 0},
    {'entries': [{'token': '../private', 'kind': 'directory'}], 'unknown': 0},
    {'entries': [{'token': 'a' * 32, 'kind': 'owned'}], 'unknown': 0},
    {'entries': [{'token': 'a' * 32, 'kind': 'directory', 'path': '/secret'}], 'unknown': 0},
    {'entries': [{'token': 'a' * 32, 'kind': 'directory'}] * 2, 'unknown': 0},
    {'entries': [{'token': token * 32, 'kind': 'directory'} for token in ('b', 'a')], 'unknown': 0},
])
def test_malformed_worker_output_is_rejected(monkeypatch, response):
    from network import worker
    def run(target, arguments, **kwargs):
        if 'entries' in response and 'ok' not in response:
            return dict(response, ok=True, request=arguments[0])
        return response
    monkeypatch.setattr(worker, 'run', run)
    with pytest.raises(RuntimeError):
        workspace.inspect_workspaces(timeout=30)


def test_linked_namespace_does_not_expose_target(tmp_path, monkeypatch):
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / ('opskit-sandbox-' + 'a' * 32)).mkdir()
    platform = tmp_path / 'platform'
    platform.mkdir()
    try:
        (platform / 'plugin-runtimes').symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(platform))
    with pytest.raises(RuntimeError, match='changed'):
        workspace.inspect_workspaces(timeout=30)
    assert len(list(foreign.iterdir())) == 1


@pytest.mark.skipif(os.name != 'posix', reason='POSIX FIFO')
def test_fifo_is_observed_without_opening(tmp_path):
    parent = namespace(tmp_path)
    path = parent / ('opskit-sandbox-' + 'a' * 32)
    os.mkfifo(path)
    assert workspace.inspect_workspaces(timeout=30) == {
        'entries': [{'token': 'a' * 32, 'kind': 'unsafe'}], 'unknown': 0}
    assert path.exists()


_inspect_worker = workspace._inspect_workspaces_worker


def blocked_scan_worker(connection, *arguments):
    original = os.scandir
    def scan(path):
        if Path(path).name == 'workspaces':
            block()
        return original(path)
    os.scandir = scan
    _inspect_worker(connection, *arguments)


def denied_scan_worker(connection, *arguments):
    original = os.scandir
    def scan(path):
        if Path(path).name == 'workspaces':
            raise PermissionError('/private/secret metadata')
        return original(path)
    os.scandir = scan
    _inspect_worker(connection, *arguments)


def changed_scan_worker(connection, *arguments):
    from contextlib import contextmanager
    original = os.scandir
    @contextmanager
    def scan(path):
        with original(path) as children:
            yield children
        if Path(path).name == 'workspaces':
            (Path(path) / 'created-during-inspection').write_bytes(b'preserve')
    os.scandir = scan
    _inspect_worker(connection, *arguments)


def test_directory_change_rejects_snapshot_and_preserves_new_entry(tmp_path, monkeypatch):
    parent = namespace(tmp_path)
    monkeypatch.setattr(workspace, '_inspect_workspaces_worker', changed_scan_worker)
    with pytest.raises(RuntimeError, match='changed'):
        workspace.inspect_workspaces(timeout=30)
    assert (parent / 'created-during-inspection').read_bytes() == b'preserve'


@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_scan_is_reaped_and_preserves_evidence(
        tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    parent = namespace(tmp_path)
    evidence = parent / 'evidence'
    evidence.write_bytes(b'keep')
    marker = tmp_path / 'inspection.pid'
    monkeypatch.setenv('OPSKIT_TEST_WORKSPACE_PID', str(marker))
    monkeypatch.setattr(workspace, '_inspect_workspaces_worker', blocked_scan_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        workspace.inspect_workspaces(timeout=30)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert evidence.read_bytes() == b'keep'


def test_cli_readonly_output_needs_no_docker_and_hides_unknown_names(tmp_path, monkeypatch):
    import main
    from core import plugin_sandbox
    parent = namespace(tmp_path)
    (parent / ('opskit-sandbox-' + 'a' * 32)).mkdir()
    (parent / 'private-name-secret').write_bytes(b'keep')
    monkeypatch.setattr(plugin_sandbox, 'docker_command', lambda: pytest.fail('inspection used Docker'))
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'inspect-workspaces'])
        assert result.exit_code == 0, result.output
        value = json.loads(result.output)
        assert value['ok'] is True and value['data'] == {
            'entries': [{'token': 'a' * 32, 'kind': 'directory'}], 'unknown': 1}
        assert 'private-name-secret' not in result.output
    finally:
        configure('text', False)


@pytest.mark.parametrize('limit', ['0', '257', 'nonnumeric'])
def test_cli_invalid_limit_is_usage_error_without_worker(monkeypatch, limit):
    import main
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: pytest.fail('invalid CLI started worker'))
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'inspect-workspaces', '--limit', limit])
        assert result.exit_code == 2, result.output
        assert json.loads(result.output)['error']['code'] == 2
    finally:
        configure('text', False)


def test_cli_filesystem_error_is_operational_and_redacted(tmp_path, monkeypatch):
    import main
    namespace(tmp_path)
    monkeypatch.setattr(workspace, '_inspect_workspaces_worker', denied_scan_worker)
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'inspect-workspaces'])
        assert result.exit_code == 1, result.output
        value = json.loads(result.output)
        assert value['error']['code'] == 1 and value['data'] is None
        assert 'unconfirmed' in value['error']['message'] and '/private' not in result.output
    finally:
        configure('text', False)


def test_cli_cancel_is_130(monkeypatch):
    import main
    def cancelled(**kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(workspace, 'inspect_workspaces', cancelled)
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'inspect-workspaces'])
        assert result.exit_code == 130, result.output
    finally:
        configure('text', False)
