"""An allocation intent preserves evidence without granting directory ownership."""
import json
from pathlib import Path

import pytest

from core import plugin_resource_io as io, plugin_resources as resources, plugin_workspace as workspace
from test_plugin_resource_journal import Daemon


def intent_record():
    return {'schema': 3, 'token': 'a' * 32, 'kind': 'container',
            'name': 'opskit-' + 'b' * 32, 'backend': ['docker'], 'daemon': 'daemon-a',
            'workspace_intent': {'name': 'opskit-sandbox-' + 'c' * 32, 'parent': [1, 2]}}


def test_intent_schema_has_no_directory_ownership():
    record = intent_record()
    assert resources._record_value(Path('a' * 32 + '.json'), record) == record
    root, parent = workspace.recorded_workspace_intent(record['workspace_intent'])
    assert root.name == 'opskit-sandbox-' + 'c' * 32 and parent == [1, 2]
    with pytest.raises(ValueError):
        workspace.recorded_workspace(record['workspace_intent'])
    with pytest.raises(ValueError, match='ownership'):
        workspace.cleanup(record['workspace_intent'], timeout=30)


@pytest.mark.parametrize('field,value', [
    ('name', '../escape'), ('name', 'opskit-sandbox-' + 'A' * 32),
    ('name', None), ('parent', [True, 2]), ('parent', [1, 0]),
    ('parent', None), ('identity', [1, 2]), ('extra', 'secret')])
def test_invalid_intent_cannot_be_a_resource_record(field, value):
    record = intent_record()
    record['workspace_intent'][field] = value
    with pytest.raises(ValueError):
        resources._record_value(Path('a' * 32 + '.json'), record)


@pytest.mark.parametrize('mutation', ['network', 'bound', 'missing', 'schema_bool'])
def test_intent_record_is_strict(mutation):
    record = intent_record()
    if mutation == 'network':
        record['kind'] = 'network'
    elif mutation == 'bound':
        record['workspace'] = {'name': 'opskit-sandbox-' + 'c' * 32, 'parent': [1, 2], 'identity': [1, 3]}
    elif mutation == 'missing':
        record.pop('workspace_intent')
    else:
        record['schema'] = True
    with pytest.raises(ValueError):
        resources._record_value(Path('a' * 32 + '.json'), record)


@pytest.mark.parametrize('change', ['name', 'parent', 'journal'])
def test_binding_cannot_switch_the_recorded_attempt(change):
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    owned = workspace.allocate(timeout=30)
    pending = io.record_workspace_intent(lease.record, owned.root.name, owned.parent_identity)
    raw = lease.path.read_bytes()
    candidate = owned
    if change == 'name':
        candidate = workspace.Workspace(owned.root.with_name('opskit-sandbox-' + 'b' * 32),
                                        owned.parent_identity, owned.identity)
    elif change == 'parent':
        candidate = workspace.Workspace(owned.root, [owned.parent_identity[0], owned.parent_identity[1] + 1],
                                        owned.identity)
    else:
        lease.path.write_text(json.dumps(dict(pending, daemon='daemon-b')), encoding='utf-8')
    expected = lease.path.read_bytes()
    try:
        with pytest.raises(ValueError):
            io.bind_workspace_record(pending, candidate)
        assert lease.path.read_bytes() == expected
        assert owned.root.is_dir()
    finally:
        lease.path.write_bytes(raw)
        # Finish the original attempt so normal lease ownership can clean it.
        io.bind_workspace_record(pending, owned)
        lease.close(daemon)
    assert not owned.root.exists() and not lease.path.exists()


def test_duplicate_attempt_cannot_replace_first_intent():
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    owned = workspace.allocate(timeout=30)
    pending = io.record_workspace_intent(lease.record, owned.root.name, owned.parent_identity)
    raw = lease.path.read_bytes()
    try:
        with pytest.raises(ValueError, match='unbound container lease'):
            io.record_workspace_intent(pending, 'opskit-sandbox-' + 'b' * 32, owned.parent_identity)
        # A stale schema1 caller cannot overwrite the schema3 disk record either.
        with pytest.raises(ValueError, match='changed before binding'):
            io.record_workspace_intent(lease.record, 'opskit-sandbox-' + 'b' * 32, owned.parent_identity)
        assert lease.path.read_bytes() == raw
    finally:
        io.bind_workspace_record(pending, owned)
        lease.close(daemon)
