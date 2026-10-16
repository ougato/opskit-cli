"""Identity-bound temporary workspace allocation and bounded supervised removal."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import math
import multiprocessing
import time
import uuid

MAX_INSPECTION_ENTRIES = 4096

@dataclass(frozen=True)
class Workspace:
    root: Path
    parent_identity: list
    identity: list


def _inspect_workspaces_worker(connection, request, limit):
    import os
    import re
    import stat
    from core.paths import data_dir
    from core.plugin_state import _directory, _plain
    from core.plugin_workspace_budget import _version
    from network.worker import send
    try:
        parent = data_dir().absolute() / 'plugin-runtimes' / 'workspaces'
        if '..' in parent.parts:
            raise ValueError('noncanonical workspace namespace')
        entries, unknown = [], 0
        for ancestor in reversed((parent, *parent.parents)):
            try:
                _directory(ancestor)
            except FileNotFoundError:
                break
        else:
            before = parent.lstat()
            with os.scandir(parent) as children:
                for index, child in enumerate(children):
                    if index >= MAX_INSPECTION_ENTRIES:
                        raise OverflowError()
                    match = re.fullmatch(r'opskit-sandbox-([0-9a-f]{32})', child.name)
                    if match is None:
                        unknown += 1
                        continue
                    if len(entries) >= limit:
                        raise OverflowError()
                    info = (parent / child.name).lstat()
                    entries.append({'token': match[1], 'kind':
                                    'directory' if _plain(info, stat.S_ISDIR) else 'unsafe'})
            if _version(before) != _version(parent.lstat()):
                raise ValueError('workspace inventory changed')
            for ancestor in (parent, *parent.parents):
                _directory(ancestor)
        result = {'ok': True, 'request': request,
                  'entries': sorted(entries, key=lambda item: item['token']), 'unknown': unknown}
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


def inspect_workspaces(*, timeout, limit=256):
    """Observe direct entries, never infer ownership or authorize deletion.

    No recursive reads, resource journals, locks, Docker calls or mutations.
    O(min(direct entries, 4097)) scan time; at most 256 returned observations.
    """
    import re
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid workspace inspection allowance')
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError('invalid workspace inspection capacity')
    request = uuid.uuid4().hex
    result = run(_inspect_workspaces_worker, (request, limit), timeout=timeout,
                 label='plugin workspace inspection')
    for reason in ('limit', 'filesystem', 'changed'):
        if result == {'ok': False, 'reason': reason}:
            raise RuntimeError('workspace inspection unconfirmed: ' + reason)
    if (not isinstance(result, dict) or set(result) != {'ok', 'request', 'entries', 'unknown'}
            or result['ok'] is not True or result['request'] != request):
        raise RuntimeError('workspace inspection unconfirmed')
    entries, unknown = result['entries'], result['unknown']
    if (not isinstance(entries, list) or len(entries) > limit or type(unknown) is not int
            or not 0 <= unknown <= MAX_INSPECTION_ENTRIES - len(entries)):
        raise RuntimeError('invalid workspace inventory')
    tokens = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'token', 'kind'}
                or not isinstance(entry['token'], str)
                or re.fullmatch(r'[0-9a-f]{32}', entry['token']) is None
                or entry['kind'] not in ('directory', 'unsafe')):
            raise RuntimeError('invalid workspace observation')
        tokens.append(entry['token'])
    if tokens != sorted(set(tokens)):
        raise RuntimeError('invalid workspace token inventory')
    return {'entries': entries, 'unknown': unknown}


def recorded_workspace_intent(value):
    """Resolve an allocation attempt; it grants no directory ownership."""
    import re
    from core.paths import data_dir
    from core.plugin_state import _valid_identity
    if (not isinstance(value, dict) or set(value) != {"name", "parent"}
            or not isinstance(value["name"], str)
            or re.fullmatch(r"opskit-sandbox-[0-9a-f]{32}", value["name"]) is None
            or not _valid_identity(value["parent"])):
        raise ValueError("invalid workspace allocation intent")
    root = data_dir().absolute() / "plugin-runtimes" / "workspaces" / value["name"]
    return root, value['parent']


def recorded_workspace(value):
    """Resolve a strict identity binding in the current platform namespace."""
    from core.plugin_state import _valid_identity
    if (not isinstance(value, dict) or set(value) != {'name', 'parent', 'identity'}
            or not _valid_identity(value['identity'])):
        raise ValueError('invalid workspace journal binding')
    root, parent = recorded_workspace_intent({key: value[key] for key in ('name', 'parent')})
    return Workspace(root, parent, value['identity'])


def journal_binding(workspace):
    if not isinstance(workspace, Workspace):
        raise ValueError("invalid workspace ownership")
    value = {"name": workspace.root.name, "parent": workspace.parent_identity,
             "identity": workspace.identity}
    if recorded_workspace(value) != workspace:
        raise ValueError("workspace outside platform namespace")
    return value


def _allocate_worker(connection, request, buffer=None, length=0, resource_record=None):
    import os
    import stat
    from core.plugin_state import _directory
    from core.plugin_state_budget import platform_state_guard
    from core.plugin_workspace_budget import reserve_workspace
    from core.paths import data_dir
    from network.worker import send
    try:
        # One persistent namespace per platform, independent of TEMP/TMPDIR.
        # Validate ancestors before creation; never resolve through host links.
        parent = data_dir().absolute() / "plugin-runtimes" / "workspaces"
        if parent.resolve() != parent:
            raise ValueError("workspace parent must be canonical")
        root = parent / ("opskit-sandbox-" + request)
        with platform_state_guard():
            for directory in (parent, *parent.parents):
                try:
                    _directory(directory)
                except FileNotFoundError:
                    pass
            for directory in reversed((parent, *parent.parents)):
                try:
                    _directory(directory)
                except FileNotFoundError:
                    try:
                        directory.mkdir(mode=0o700)
                    except FileExistsError:
                        pass
                    _directory(directory)
            parent_identity = _directory(parent)
            reserve_workspace(parent)
            if resource_record is not None:
                from core.plugin_resource_io import record_workspace_intent
                resource_record = record_workspace_intent(resource_record, root.name, parent_identity)
            root.mkdir(mode=0o700)
            identity = _directory(root)
        if resource_record is not None:
            from core.plugin_resource_io import bind_workspace_record
            bind_workspace_record(resource_record, Workspace(root, parent_identity, identity))
        if buffer is not None:
            target = root / "request.json"
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o644)
            with os.fdopen(descriptor, "wb") as stream:
                before = os.fstat(stream.fileno())
                view = memoryview(buffer).cast("B")
                try:
                    if stream.write(view[:length]) != length:
                        raise OSError("incomplete request write")
                finally:
                    view.release()
                stream.flush()
                if os.name == "posix":
                    # The container's unprivileged UID must read its bind mount.
                    # The host's private workspace parent restricts discovery.
                    os.fchmod(stream.fileno(), 0o444)
                current = target.lstat()
                if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                        or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                        or current.st_size != length):
                    raise ValueError("request identity changed")
        if _directory(root) != identity:
            raise ValueError("workspace identity changed")
        if _directory(parent) != parent_identity:
            raise ValueError("temporary parent changed")
        result = {"ok": True, "request": request, "root": str(root),
                  "parent": parent_identity, "identity": identity}
    except (OSError, ValueError, RuntimeError):
        result = {"ok": False}
    try:
        send(connection, result)
    finally:
        connection.close()


def allocate(*, timeout, request_text=None, resource_record=None):
    from core.plugin_json import MAX_TEXT
    from core.plugin_state import _valid_identity
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid workspace allowance")
    deadline = time.monotonic() + timeout
    arguments = ()
    if request_text is not None:
        if not isinstance(request_text, str) or not 0 < len(request_text) <= MAX_TEXT:
            raise ValueError("invalid workspace request")
        encoded = request_text.encode("utf-8")
        if len(encoded) > MAX_TEXT:
            raise ValueError("workspace request exceeds byte limit")
        buffer = multiprocessing.get_context("spawn").Array("B", len(encoded), lock=False)
        view = memoryview(buffer).cast("B")
        try:
            view[:] = encoded
        finally:
            view.release()
        arguments = (buffer, len(encoded))
    if resource_record is not None:
        arguments = (*(arguments or (None, 0)), resource_record)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("plugin workspace allocation deadline exceeded")
    request = uuid.uuid4().hex
    result = run(_allocate_worker, (request, *arguments), timeout=remaining, label="plugin workspace allocation")
    if (not isinstance(result, dict)
            or set(result) != {"ok", "request", "root", "parent", "identity"}
            or result["ok"] is not True or result["request"] != request
            or not isinstance(result["root"], str) or not 0 < len(result["root"]) <= 65536
            or not Path(result["root"]).is_absolute() or ".." in Path(result["root"]).parts
            or Path(result["root"]).name != "opskit-sandbox-" + request
            or not _valid_identity(result["parent"]) or not _valid_identity(result["identity"])):
        raise RuntimeError("plugin workspace allocation unconfirmed; evidence retained")
    return Workspace(Path(result["root"]), result["parent"], result["identity"])


def _cleanup_worker(connection, root, parent_identity, identity, request):
    import re
    from core.plugin_metadata import path_present, sync_directory
    from core.plugin_state import _directory, _remove_owned_tree
    from core.plugin_workspace_budget import WORKSPACE_BYTES, WORKSPACE_ENTRIES, MAX_DEPTH
    from network.worker import send
    try:
        target = Path(root)
        if (not target.is_absolute() or target.parent.resolve() != target.parent
                or re.fullmatch(r"opskit-sandbox-[0-9a-f]{32}", target.name) is None
                or _directory(target.parent) != parent_identity):
            raise ValueError("workspace ownership changed")
        if path_present(target):
            _remove_owned_tree(target, tuple(identity), lambda: None,
                               max_entries=WORKSPACE_ENTRIES - 1, max_bytes=WORKSPACE_BYTES,
                               max_files=8193, max_depth=MAX_DEPTH)
        sync_directory(target.parent, parent_identity)
        if path_present(target) or _directory(target.parent) != parent_identity:
            raise ValueError('workspace reappeared during cleanup sync')
        result = {"ok": True, "request": request, "root": root}
    except (OSError, ValueError):
        result = {"ok": False}
    try:
        send(connection, result)
    finally:
        connection.close()


def cleanup(workspace, *, timeout):
    from core.plugin_state import _valid_identity
    from network.worker import run
    if (not isinstance(workspace, Workspace) or not _valid_identity(workspace.parent_identity)
            or not _valid_identity(workspace.identity)):
        raise ValueError("invalid workspace ownership")
    request = uuid.uuid4().hex
    root = str(workspace.root)
    result = run(_cleanup_worker, (root, workspace.parent_identity, workspace.identity, request),
                 timeout=timeout, label="plugin workspace cleanup")
    if (not isinstance(result, dict) or set(result) != {"ok", "request", "root"}
            or result["ok"] is not True or result["request"] != request or result["root"] != root):
        raise RuntimeError("plugin workspace cleanup unconfirmed; evidence retained")


@contextmanager
def owned_workspace(*, timeout, cleanup_timeout, request_text=None, resource=None):
    from core.plugin_sandbox import SandboxError
    try:
        workspace = (allocate(timeout=timeout, request_text=request_text) if resource is None
                     else resource.allocate_workspace(timeout=timeout, request_text=request_text))
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        raise SandboxError("plugin workspace allocation failed; evidence retained") from exc
    failure = None
    try:
        yield workspace
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            if resource is None:
                cleanup(workspace, timeout=cleanup_timeout)
        except BaseException as exc:
            if failure is None:
                if isinstance(exc, Exception):
                    raise SandboxError("plugin workspace cleanup incomplete; evidence retained") from exc
                raise
            from core.cleanup import add_exception_note
            add_exception_note(failure, "plugin workspace cleanup incomplete; evidence retained")
