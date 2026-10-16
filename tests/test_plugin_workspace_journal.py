"""Workspace recovery requires a bound journal, inactive lease and same daemon."""
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from core import plugin_resources as resources, plugin_workspace as workspace
from test_plugin_resource_journal import Daemon


def bound_lease():
    daemon = Daemon()
    owned = workspace.allocate(timeout=30)
    (owned.root / 'evidence').write_bytes(b'keep')
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32,
                                    daemon, workspace=owned)
    return daemon, owned, lease


def test_active_workspace_is_not_recovered():
    daemon, owned, lease = bound_lease()
    try:
        assert resources.reconcile(['docker'], daemon)['active'] == 1
        assert (owned.root / 'evidence').read_bytes() == b'keep'
        assert lease.record['schema'] == 2
        assert resources._read(lease.path) == lease.record
    finally:
        lease.close(daemon)
    assert not owned.root.exists() and not lease.path.exists()


@pytest.mark.parametrize('replacement', ['target', 'parent'])
def test_replaced_directory_is_retained_with_journal(replacement):
    daemon, owned, lease = bound_lease()
    lease.lock.__exit__(None, None, None)
    target = owned.root if replacement == 'target' else owned.root.parent
    original = target.with_name(target.name + '-original')
    target.rename(original)
    target.mkdir()
    (target / 'unrelated').write_bytes(b'keep replacement')
    try:
        result = resources.reconcile(['docker'], daemon)
        assert result == {'recovered': 0, 'active': 0, 'pending': 1, 'invalid': 0}
        assert (target / 'unrelated').read_bytes() == b'keep replacement'
        assert lease.path.exists()
    finally:
        (target / 'unrelated').unlink()
        target.rmdir()
        original.rename(target)
    assert resources.reconcile(['docker'], daemon)['recovered'] == 1
    assert not owned.root.exists()


def test_failed_workspace_cleanup_keeps_record_for_retry(monkeypatch):
    daemon, owned, lease = bound_lease()
    lease.lock.__exit__(None, None, None)
    original = lease.path.read_bytes()
    def denied(*args, **kwargs):
        raise RuntimeError('workspace cleanup unconfirmed')
    with monkeypatch.context() as patch:
        patch.setattr(workspace, 'cleanup', denied)
        assert resources.reconcile(['docker'], daemon)['pending'] == 1
    assert lease.path.read_bytes() == original
    assert (owned.root / 'evidence').read_bytes() == b'keep'
    assert resources.reconcile(['docker'], daemon)['recovered'] == 1
    assert not owned.root.exists()


@pytest.mark.parametrize('kind', ['container', 'network'])
@pytest.mark.parametrize('resource_present', [False, True])
def test_daemon_switch_during_successful_cleanup_retains_intent(kind, resource_present):
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], kind, 'opskit-' + 'a' * 32, daemon)
    if resource_present:
        daemon.objects.append({'id': 'b' * 64, 'name': lease.record['name'],
                               'kind': kind, 'token': lease.record['token']})
    def changed(*args):
        response = daemon(*args)
        if args[0] == 'rm' or args[:2] == ('network', 'rm') or (not resource_present and args[1] == 'ls'):
            daemon.identity = 'daemon-b'
        return response
    with pytest.raises(RuntimeError, match='backend changed'):
        lease.close(changed)
    assert lease.path.exists()
    daemon.identity = 'daemon-a'
    assert resources.reconcile(['docker'], daemon)['recovered'] == 1


@pytest.mark.parametrize('field,value', [('name', '../other'), ('parent', [0, 0]),
                                        ('identity', None), ('extra', 1)])
def test_invalid_workspace_binding_rejected(field, value):
    owned = workspace.allocate(timeout=30)
    try:
        binding = workspace.journal_binding(owned)
        binding[field] = value
        record = {'schema': 2, 'token': 'a' * 32, 'kind': 'container',
                  'name': 'opskit-' + 'b' * 32, 'backend': ['docker'],
                  'daemon': 'daemon-a', 'workspace': binding}
        with pytest.raises(ValueError):
            resources._record_value(Path('a' * 32 + '.json'), record)
    finally:
        workspace.cleanup(owned, timeout=30)


def _crash_owner(connection):
    daemon, owned, lease = bound_lease()
    connection.send({'root': str(owned.root), 'path': str(lease.path)})
    connection.close()
    os._exit(17)


def test_crashed_owner_workspace_is_recovered_from_durable_binding():
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_crash_owner, args=(sender,))
    process.start()
    sender.close()
    try:
        assert receiver.poll(60), 'owner did not publish its durable binding'
        record = receiver.recv()
        process.join(30)
        assert process.exitcode == 17
        root, path = Path(record['root']), Path(record['path'])
        assert (root / 'evidence').read_bytes() == b'keep'
        assert json.loads(path.read_bytes())['schema'] == 2
        result = resources.reconcile(['docker'], Daemon())
        assert result == {'recovered': 1, 'active': 0, 'pending': 0, 'invalid': 0}
        assert not root.exists() and not path.exists()
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(10)
        process.close()
