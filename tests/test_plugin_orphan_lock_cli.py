"""Operator metadata recovery cannot delete active leases or journal evidence."""
import json

import pytest
from typer.testing import CliRunner

from core import plugin_resource_io as io, plugin_resources as resources, plugin_sandbox
from core.cli import configure
from core.resource_lock import ResourceLock


TOKEN = 'a' * 32


@pytest.mark.parametrize('state', ['orphan', 'absent', 'recorded', 'busy', 'index'])
def test_exact_token_recovery_preserves_evidence_without_docker(tmp_path, monkeypatch, state):
    import main
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(plugin_sandbox, 'docker_command', lambda: pytest.fail('metadata retirement needs no Docker'))
    root = resources._root()
    path = root / (TOKEN + '.lock')
    if state != 'absent':
        path.write_bytes(b'owned metadata')
    journal = path.with_suffix('.json')
    if state == 'recorded':
        journal.write_bytes(b'corrupt but indispensable evidence')
    held = None
    if state in ('busy', 'index'):
        held = ResourceLock(lambda: path if state == 'busy' else root / '.index.lock', label='live owner').hold()
        held.__enter__()
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'retire-lock', TOKEN])
        retained = state in ('recorded', 'busy', 'index')
        assert result.exit_code == int(retained), result.output
        value = json.loads(result.output)
        assert value['ok'] is (not retained)
        assert value['data'] == {'token': TOKEN, 'status': 'retained' if retained else 'absent' if state == 'absent' else 'retired'}
        assert path.exists() is retained
        if state == 'recorded':
            assert journal.read_bytes() == b'corrupt but indispensable evidence'
        assert (root / '.index.lock').exists()
    finally:
        if held is not None:
            held.__exit__(None, None, None)
        configure('text', False)


@pytest.mark.parametrize('token', ['../escape', 'A' * 32, 'a' * 31, 'a' * 33, '/tmp/lock', '--help\n'])
def test_invalid_token_never_starts_filesystem_work(monkeypatch, token):
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: pytest.fail('invalid token started worker'))
    with pytest.raises(ValueError, match='token'):
        io.retire_orphan_lock(token, timeout=30)


def test_token_worker_rejects_linked_metadata_and_keeps_target(tmp_path, monkeypatch):
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(tmp_path / 'platform'))
    root = resources._root()
    target = tmp_path / 'foreign'
    target.write_bytes(b'foreign')
    path = root / (TOKEN + '.lock')
    # Hard links work without Windows developer-mode symlink privileges.
    import os
    os.link(target, path)
    with pytest.raises(ValueError, match='changed'):
        io.retire_orphan_lock(TOKEN, timeout=30)
    assert path.read_bytes() == target.read_bytes() == b'foreign'


def test_cli_invalid_token_is_usage_error_without_worker(monkeypatch):
    import main
    from network import worker
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: pytest.fail('invalid CLI token started worker'))
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'retire-lock', '../escape'])
        assert result.exit_code == 2, result.output
        value = json.loads(result.output)
        assert value['ok'] is False and value['error']['code'] == 2
    finally:
        configure('text', False)


def test_metadata_failure_is_operational_and_does_not_expose_paths(monkeypatch):
    import main
    def changed(*args, **kwargs):
        raise ValueError('/private/sensitive/path changed')
    monkeypatch.setattr(io, 'retire_orphan_lock', changed)
    try:
        result = CliRunner().invoke(main.app, ['--output', 'json', 'plugin', 'retire-lock', TOKEN])
        assert result.exit_code == 1, result.output
        value = json.loads(result.output)
        assert value['ok'] is False and value['error']['code'] == 1
        assert 'unconfirmed' in value['error']['message'] and '/private' not in result.output
    finally:
        configure('text', False)
