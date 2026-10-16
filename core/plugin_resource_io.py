"""Bounded filesystem mutations for identity-checked resource journal records."""
from pathlib import Path
import math
import re
import uuid

from core.plugin_metadata import path_present as _present

MAX_INVENTORY_ENTRIES = 4096


def _confirm_record(path, record, parent):
    from core.plugin_metadata import sync_directory
    from core.plugin_resources import _read, _journal_version
    from core.plugin_state import _directory
    version = _journal_version(path.lstat())
    if _read(path) != record or _directory(path.parent) != parent:
        raise ValueError('resource journal changed before directory sync')
    sync_directory(path.parent, parent)
    if (_journal_version(path.lstat()) != version or _read(path) != record
            or _directory(path.parent) != parent):
        raise ValueError('resource journal changed during directory sync')


def _replace_workspace_record(record, updated):
    """Worker-only transition while the parent holds the resource OS lease."""
    import json
    from core.plugin_resources import _root, _record_value, _read, _journal_version
    from core.plugin_workspace import recorded_workspace, recorded_workspace_intent
    from core.plugin_state import _directory
    from core.resource_lock import ResourceLock
    from core.secrets import atomic_write
    from core.plugin_metadata import sync_directory
    root = _root(create=False)
    path = root / (record['token'] + '.json')
    _record_value(path, record)
    _record_value(path, updated)
    if updated['schema'] == 2:
        workspace = recorded_workspace(updated['workspace'])
        workspace_root, workspace_parent = workspace.root, workspace.parent_identity
    else:
        workspace = None
        workspace_root, workspace_parent = recorded_workspace_intent(updated['workspace_intent'])

    def confirm_workspace():
        if (_directory(workspace_root.parent) != workspace_parent
                or workspace is not None and _directory(workspace.root) != workspace.identity):
            raise ValueError('resource or workspace changed during transition')

    parent = _directory(root)
    with ResourceLock(lambda: root / '.index.lock', label='plugin resource index').hold():
        sync_directory(workspace_root.parent, workspace_parent)
        version = _journal_version(path.lstat())
        if (_read(path) != record or _journal_version(path.lstat()) != version
                or _directory(root) != parent):
            raise ValueError('resource or workspace changed before binding')
        confirm_workspace()
        atomic_write(path, json.dumps(updated))
        _confirm_record(path, updated, parent)
        confirm_workspace()
    return updated


def record_workspace_intent(record, name, parent):
    """Persist the attempted namespace before mkdir, without claiming ownership."""
    if record['schema'] != 1 or record['kind'] != 'container':
        raise ValueError('workspace intent requires an unbound container lease')
    return _replace_workspace_record(record, dict(record, schema=3,
        workspace_intent={'name': name, 'parent': parent}))


def bind_workspace_record(record, workspace):
    """Upgrade only the same pre-recorded attempt to an identity-bound lease."""
    from core.plugin_workspace import journal_binding
    binding = journal_binding(workspace)
    if (record['schema'] != 3 or record['kind'] != 'container'
            or record['workspace_intent'] != {key: binding[key] for key in ('name', 'parent')}):
        raise ValueError('workspace differs from recorded allocation intent')
    original = {key: value for key, value in record.items() if key != 'workspace_intent'}
    return _replace_workspace_record(record, dict(original, schema=2, workspace=binding))


def _metadata_worker(connection, request, limit):
    import os
    from core.plugin_resources import _root
    from core.plugin_state import _directory
    from core.plugin_invocation_lock import _file_identity
    from network.worker import send
    try:
        root = _root(create=False)
        try:
            parent = _directory(root)
        except FileNotFoundError:
            result = {'ok': True, 'request': request, 'entries': [], 'unknown': 0}
        else:
            tokens, unknown = set(), 0
            with os.scandir(root) as directory:
                for index, entry in enumerate(directory):
                    if index >= MAX_INVENTORY_ENTRIES:
                        raise OverflowError()
                    if entry.name == '.index.lock':
                        continue
                    match = re.fullmatch(r'([0-9a-f]{32})\.(?:json|lock)', entry.name)
                    if match is None:
                        unknown += 1
                        continue
                    tokens.add(match[1])
                    if len(tokens) > limit:
                        raise OverflowError()
            entries = []
            for token in sorted(tokens):
                # Existence alone is enough to preserve journal evidence. Do
                # not parse corrupt contents or follow linked journal targets.
                journal = _present(root / (token + '.json'))
                try:
                    _file_identity((root / (token + '.lock')).lstat())
                except FileNotFoundError:
                    lock = 'absent'
                except ValueError:
                    lock = 'unsafe'
                else:
                    lock = 'present'
                entries.append({'token': token, 'journal': journal, 'lock': lock})
            if _directory(root) != parent:
                raise ValueError('resource journal root changed')
            result = {'ok': True, 'request': request, 'entries': entries, 'unknown': unknown}
    except OverflowError:
        result = {'ok': False, 'reason': 'limit'}
    except OSError:
        result = {'ok': False, 'reason': 'filesystem'}
    except (ValueError, RuntimeError):
        result = {'ok': False, 'reason': 'changed'}
    try:
        send(connection, result)
    finally:
        connection.close()


def inspect_lock_metadata(*, timeout, limit=256):
    """Bounded observations only: no token locking, creation or retirement."""
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid resource metadata inspection allowance')
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError('invalid resource metadata inspection capacity')
    request = uuid.uuid4().hex
    result = run(_metadata_worker, (request, limit), timeout=timeout,
                 label='plugin resource metadata inspection')
    for reason, message in (('limit', 'resource metadata exceeds inspection limit'),
                            ('filesystem', 'resource metadata inspection failed'),
                            ('changed', 'resource metadata changed during inspection')):
        if result == {'ok': False, 'reason': reason}:
            raise RuntimeError(message)
    if (not isinstance(result, dict) or set(result) != {'ok', 'request', 'entries', 'unknown'}
            or result['ok'] is not True or result['request'] != request):
        raise RuntimeError('resource metadata inspection unconfirmed')
    entries, unknown = result['entries'], result['unknown']
    if (not isinstance(entries, list) or len(entries) > limit or type(unknown) is not int
            or not 0 <= unknown <= MAX_INVENTORY_ENTRIES - len(entries)):
        raise RuntimeError('invalid resource metadata inspection')
    tokens = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'token', 'journal', 'lock'}
                or not isinstance(entry['token'], str) or re.fullmatch(r'[0-9a-f]{32}', entry['token']) is None
                or type(entry['journal']) is not bool or entry['lock'] not in ('present', 'absent', 'unsafe')):
            raise RuntimeError('invalid resource metadata entry')
        tokens.append(entry['token'])
    if tokens != sorted(set(tokens)):
        raise RuntimeError('invalid resource metadata token inventory')
    return {'entries': entries, 'unknown': unknown}


def _retire_worker(connection, source, request):
    from core.plugin_resources import _root
    from core.plugin_state import _directory
    from core.plugin_invocation_lock import _file_identity
    from core.resource_lock import ResourceLock, ResourceBusyError
    from network.worker import send
    try:
        root = _root()
        if isinstance(source, dict):
            if (set(source) != {'token'} or not isinstance(source['token'], str)
                    or re.fullmatch(r'[0-9a-f]{32}', source['token']) is None):
                raise ValueError('invalid resource metadata token')
            path = root / (source['token'] + '.json')
        else:
            path = Path(source)
        if path.parent != root:
            raise ValueError('resource journal root changed')
        parent = _directory(root)
        lock_path = path.with_suffix('.lock')
        status = 'retained'
        with ResourceLock(lambda: root / '.index.lock', label='plugin resource index').hold():
            if _directory(root) != parent:
                raise ValueError('resource journal root changed')
            if not _present(path):
                try:
                    identity = _file_identity(lock_path.lstat())
                except FileNotFoundError:
                    status = 'absent'
                else:
                    with ResourceLock(lambda: lock_path, label='plugin resource').hold():
                        if _file_identity(lock_path.lstat()) != identity:
                            raise ValueError('resource lock changed')
                    # Windows requires closing our token handle before unlink.
                    # Index remains held: a cooperating new creator that opens
                    # this token now cannot publish an intent until we finish.
                    if (_present(path) or _directory(root) != parent
                            or _file_identity(lock_path.lstat()) != identity):
                        raise ValueError('resource lock changed before retirement')
                    lock_path.unlink()
                    status = 'retired'
        result = {'ok': True, 'request': request, 'path': source, 'status': status}
    except ResourceBusyError:
        result = {'ok': True, 'request': request, 'path': source, 'status': 'retained'}
    except OSError:
        result = {'ok': False, 'reason': 'filesystem'}
    except (ValueError, RuntimeError):
        result = {'ok': False, 'reason': 'changed'}
    try:
        send(connection, result)
    finally:
        connection.close()


def retire_lock(path, *, timeout):
    """Best-effort retirement of one known token; never remove its journal."""
    source = str(path)
    if (not 0 < len(source) <= 65536 or '\0' in source or not Path(source).is_absolute()
            or '..' in Path(source).parts or re.fullmatch(r'[0-9a-f]{32}\.json', Path(source).name) is None):
        raise ValueError('invalid resource metadata retirement path')
    return _retire(source, timeout=timeout)


def retire_orphan_lock(token, *, timeout):
    """Operator recovery by exact token; resolve all paths inside the worker."""
    if not isinstance(token, str) or re.fullmatch(r'[0-9a-f]{32}', token) is None:
        raise ValueError('invalid resource metadata token')
    return _retire({'token': token}, timeout=timeout)


def _retire(source, *, timeout):
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid resource metadata retirement allowance')
    request = uuid.uuid4().hex
    result = run(_retire_worker, (source, request), timeout=timeout,
                 label='plugin resource metadata retirement')
    if result == {'ok': False, 'reason': 'filesystem'}:
        raise OSError('resource metadata retirement failed')
    if result == {'ok': False, 'reason': 'changed'}:
        raise ValueError('resource metadata changed during retirement')
    if (not isinstance(result, dict) or set(result) != {'ok', 'request', 'path', 'status'}
            or result['ok'] is not True or result['request'] != request or result['path'] != source
            or result['status'] not in ('retained', 'retired', 'absent')):
        raise RuntimeError('resource metadata retirement unconfirmed')
    return result['status']


def _inventory_worker(connection, request, limit):
    import os
    from core.plugin_resources import _root
    from core.plugin_state import _directory
    from network.worker import send
    try:
        root = _root()
        parent = _directory(root)
        names, invalid = [], 0
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_INVENTORY_ENTRIES:
                    raise OverflowError()
                if not entry.name.lower().endswith('.json'):
                    continue
                if re.fullmatch(r'[0-9a-f]{32}\.json', entry.name):
                    names.append(entry.name)
                else:
                    invalid += 1
                if len(names) + invalid > limit:
                    raise OverflowError()
        if _directory(root) != parent:
            raise ValueError('resource journal root changed')
        result = {'ok': True, 'request': request, 'root': str(root),
                  'names': sorted(names), 'invalid': invalid}
    except OverflowError:
        result = {'ok': False, 'reason': 'limit'}
    except OSError:
        result = {'ok': False, 'reason': 'filesystem'}
    except (ValueError, RuntimeError):
        result = {'ok': False, 'reason': 'changed'}
    try:
        send(connection, result)
    finally:
        connection.close()


def inventory(*, timeout, limit):
    """One bounded scan; invalid filenames are counted, never action targets."""
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid resource journal inventory allowance')
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError('invalid resource journal capacity')
    request = uuid.uuid4().hex
    result = run(_inventory_worker, (request, limit), timeout=timeout,
                 label='plugin resource journal inventory')
    if result == {'ok': False, 'reason': 'limit'}:
        raise RuntimeError('resource journal exceeds recovery limit')
    if result == {'ok': False, 'reason': 'filesystem'}:
        raise OSError('resource journal inventory failed')
    if result == {'ok': False, 'reason': 'changed'}:
        raise ValueError('resource journal root changed')
    if (not isinstance(result, dict) or set(result) != {'ok', 'request', 'root', 'names', 'invalid'}
            or result['ok'] is not True or result['request'] != request):
        raise RuntimeError('resource journal inventory unconfirmed')
    root, names, invalid = result['root'], result['names'], result['invalid']
    if (not isinstance(root, str) or not 0 < len(root) <= 65536 or '\0' in root
            or not Path(root).is_absolute() or '..' in Path(root).parts
            or Path(root).name != 'plugin-resources'
            or not isinstance(names, list) or type(invalid) is not int or not 0 <= invalid <= limit
            or len(names) + invalid > limit
            or any(not isinstance(name, str) or re.fullmatch(r'[0-9a-f]{32}\.json', name) is None for name in names)
            or len(set(names)) != len(names)):
        raise RuntimeError('invalid resource journal inventory')
    return Path(root), names, invalid


def _create_worker(connection, source, record, request, limit):
    import json
    from itertools import islice
    from core.plugin_resources import _root, _record_value
    from core.plugin_state import _directory
    from core.resource_lock import ResourceLock, ResourceBusyError
    from core.secrets import atomic_write
    from network.worker import send
    try:
        path = Path(source)
        root = _root()
        _record_value(path, record)
        if path.parent != root:
            raise ValueError('resource journal root changed')
        parent = _directory(root)
        with ResourceLock(lambda: root / '.index.lock', label='plugin resource index').hold():
            if _directory(root) != parent or _present(path):
                raise ValueError('resource journal target changed')
            if len(list(islice(root.glob('*.json'), limit))) >= limit:
                result = {'ok': False, 'reason': 'full'}
            else:
                if _directory(root) != parent or _present(path):
                    raise ValueError('resource journal target changed before writing')
                if record['schema'] == 2:
                    from core.plugin_workspace import recorded_workspace
                    workspace = recorded_workspace(record['workspace'])
                    if (_directory(workspace.root.parent) != workspace.parent_identity
                            or _directory(workspace.root) != workspace.identity):
                        raise ValueError('workspace identity changed before recording')
                atomic_write(path, json.dumps(record))
                _confirm_record(path, record, parent)
                result = {'ok': True, 'request': request, 'path': source}
    except ResourceBusyError:
        result = {'ok': False, 'reason': 'busy'}
    except OSError:
        result = {'ok': False, 'reason': 'filesystem'}
    except (ValueError, RuntimeError):
        result = {'ok': False, 'reason': 'changed'}
    try:
        send(connection, result)
    finally:
        connection.close()


def create_record(path, record, *, timeout, limit):
    """Confirm an indexed intent before launch; uncertain writes stay recoverable."""
    from core.plugin_resources import _record_value
    from core.resource_lock import ResourceBusyError
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid resource journal creation allowance')
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError('invalid resource journal capacity')
    source = str(path)
    if (not 0 < len(source) <= 65536 or '\0' in source
            or not Path(source).is_absolute() or '..' in Path(source).parts):
        raise ValueError('invalid resource journal creation path')
    _record_value(Path(source), record)
    request = uuid.uuid4().hex
    result = run(_create_worker, (source, record, request, limit), timeout=timeout,
                 label='plugin resource journal creation')
    if result == {'ok': False, 'reason': 'full'}:
        raise RuntimeError('resource journal is full; reconcile before execution')
    if result == {'ok': False, 'reason': 'busy'}:
        raise ResourceBusyError('plugin resource index is busy; retry later')
    if result == {'ok': False, 'reason': 'filesystem'}:
        raise OSError('resource journal creation failed; recovery required')
    if result == {'ok': False, 'reason': 'changed'}:
        raise ValueError('resource journal changed during creation')
    if (not isinstance(result, dict) or set(result) != {'ok', 'request', 'path'}
            or result['ok'] is not True or result['request'] != request or result['path'] != source):
        raise RuntimeError('resource journal creation unconfirmed; recovery required')


def _confirm_absent_workspace(record):
    from core.plugin_workspace import recorded_workspace_intent
    from core.plugin_state import _directory
    root, parent = recorded_workspace_intent(record['workspace_intent'])
    if _directory(root.parent) != parent:
        raise ValueError('workspace allocation parent changed')
    if _present(root):
        raise RuntimeError('unbound workspace exists; evidence retained')
    if _directory(root.parent) != parent:
        raise ValueError('workspace allocation parent changed')


def _unlink_record(source, record, *, absent_intent=False):
    from core.plugin_resources import _root, _record_value, _read, _journal_version
    from core.plugin_state import _directory
    from core.plugin_metadata import sync_directory
    path = Path(source).absolute()
    root = _root().absolute()
    _record_value(path, record)
    if record['schema'] == 3 and not absent_intent:
        raise ValueError('unbound workspace allocation evidence cannot be retired')
    if absent_intent and record['schema'] != 3:
        raise ValueError('expected an unbound allocation intent')
    if path.parent != root:
        raise ValueError('resource journal root changed')
    if absent_intent:
        _confirm_absent_workspace(record)
    parent = _directory(root)
    version = _journal_version(path.lstat())
    if (_read(path) != record or _journal_version(path.lstat()) != version
            or _directory(root) != parent):
        raise ValueError('resource journal changed before removal')
    if absent_intent:
        _confirm_absent_workspace(record)
    path.unlink()
    sync_directory(root, parent)
    if _present(path):
        raise ValueError('resource journal reappeared during retirement')


def _remove_worker(connection, source, record, request):
    from network.worker import send
    try:
        _unlink_record(source, record)
        result = {'ok': True, 'request': request, 'path': source}
    except OSError:
        result = {'ok': False, 'reason': 'filesystem'}
    except (ValueError, RuntimeError):
        result = {'ok': False, 'reason': 'changed'}
    try:
        send(connection, result)
    finally:
        connection.close()


def _retire_absent_intent_worker(connection, source, record, request):
    from core.plugin_resources import _root
    from core.plugin_state_budget import platform_state_guard
    from core.resource_lock import ResourceLock
    from network.worker import send
    try:
        # Allocation persists its intent and performs mkdir under this guard.
        # Then index serialization excludes a late schema3 -> schema2 bind.
        # The caller continues holding the actual resource OS lease throughout.
        with platform_state_guard():
            root = _root(create=False)
            with ResourceLock(lambda: root / '.index.lock', label='plugin resource index').hold():
                _unlink_record(source, record, absent_intent=True)
        result = {'ok': True, 'request': request, 'path': source}
    except OSError:
        result = {'ok': False, 'reason': 'filesystem'}
    except ValueError:
        result = {'ok': False, 'reason': 'changed'}
    except RuntimeError:
        result = {'ok': False, 'reason': 'pending'}
    try:
        send(connection, result)
    finally:
        connection.close()


def remove_record(path, record, *, timeout):
    return _run_removal(_remove_worker, path, record, timeout)


def retire_absent_intent(path, record, *, timeout):
    """Caller holds its resource lease and has confirmed Docker cleanup."""
    if record.get('schema') != 3:
        raise ValueError('expected an unbound allocation intent')
    return _run_removal(_retire_absent_intent_worker, path, record, timeout)


def _run_removal(worker, path, record, timeout):
    from core.plugin_resources import _record_value
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid resource journal removal allowance')
    source = str(path)
    if not 0 < len(source) <= 65536 or '\0' in source:
        raise ValueError('invalid resource journal removal path')
    _record_value(Path(source), record)
    request = uuid.uuid4().hex
    result = run(worker, (source, record, request), timeout=timeout,
                 label='plugin resource journal removal')
    if result == {'ok': False, 'reason': 'pending'}:
        raise RuntimeError('workspace allocation evidence retained; retry after inspection')
    if result == {'ok': False, 'reason': 'filesystem'}:
        raise OSError('resource journal removal failed; recovery required')
    if result == {'ok': False, 'reason': 'changed'}:
        raise ValueError('resource journal changed during cleanup')
    if (not isinstance(result, dict) or set(result) != {'ok', 'request', 'path'}
            or result['ok'] is not True or result['request'] != request or result['path'] != source):
        raise RuntimeError('resource journal removal unconfirmed; recovery required')
