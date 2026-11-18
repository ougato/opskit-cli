"""Identity-bound temporary workspace allocation and bounded supervised removal."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import math
import multiprocessing
import time
import uuid

MAX_INSPECTION_ENTRIES = 4096
BUSY_RETRY_SECONDS = 5


class WorkspaceAllocationFull(RuntimeError):
    """Allocation was refused because the workspace capacity is exhausted."""


class WorkspaceAllocationBusy(RuntimeError):
    """Another process is briefly changing the workspace namespace."""

@dataclass(frozen=True)
class Workspace:
    root: Path
    parent_identity: list
    identity: list


def _workspace_parent():
    """Expand Windows short names only after rejecting linked ancestors."""
    from core.paths import data_dir
    from core.plugin_state import _directory
    parent = data_dir().absolute() / "plugin-runtimes" / "workspaces"
    if ".." in parent.parts:
        raise ValueError("noncanonical workspace namespace")
    # Resolve only after checking each existing component without following
    # links. Path.resolve() also expands legitimate Windows 8.3 aliases.
    for directory in reversed((parent, *parent.parents)):
        try:
            _directory(directory)
        except FileNotFoundError:
            break
    return parent.resolve()


def validate_binding(workspace):
    """Recheck a native workspace and its recorded parent without a journal."""
    from core.plugin_state import _directory, _valid_identity
    if (not isinstance(workspace, Workspace) or not workspace.root.is_absolute()
            or '..' in workspace.root.parts
            or not _valid_identity(workspace.identity)
            or not _valid_identity(workspace.parent_identity)):
        raise ValueError('invalid workspace binding')
    journal_binding(workspace)
    for path in (workspace.root, *workspace.root.parents):
        _directory(path)
    if (_directory(workspace.root) != workspace.identity
            or _directory(workspace.root.parent) != workspace.parent_identity):
        raise ValueError('workspace identity changed')


def recorded_workspace_intent(value):
    """Resolve an allocation attempt; it grants no directory ownership."""
    import re
    from core.plugin_state import _valid_identity
    if (not isinstance(value, dict) or set(value) != {"name", "parent"}
            or not isinstance(value["name"], str)
            or re.fullmatch(r"opskit-sandbox-[0-9a-f]{32}", value["name"]) is None
            or not _valid_identity(value["parent"])):
        raise ValueError("invalid workspace allocation intent")
    root = _workspace_parent() / value["name"]
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


def _inspect_workspaces_worker(connection, request, limit):
    import os
    import re
    import stat
    from core.plugin_state import _directory, _plain
    from core.plugin_workspace_budget import _version
    from network.worker import send
    try:
        parent = _workspace_parent()
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

    No recursive reads, ownership claims, locks or mutations.
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


def _allocate_worker(connection, request, buffer=None, length=0):
    import os
    import stat
    from core.plugin_state import _directory
    from core.plugin_state_budget import platform_state_guard
    from core.plugin_workspace_budget import reserve_workspace, WorkspaceCapacityExhausted
    from core.resource_lock import ResourceBusyError
    from network.worker import send
    try:
        # One persistent namespace per platform, independent of TEMP/TMPDIR.
        # Validate ancestors before creation; never resolve through host links.
        parent = _workspace_parent()
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
            root.mkdir(mode=0o700)
            if os.name == "nt":
                # Older CPython releases ignore mkdir's private mode on Windows.
                from core.secrets import restrict_permissions
                restrict_permissions(root)
            identity = _directory(root)
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
                    # Immutable request input; the host's private workspace
                    # parent restricts discovery independently of file mode.
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
    except WorkspaceCapacityExhausted:
        result = {"ok": False, "reason": "capacity"}
    except ResourceBusyError:
        result = {"ok": False, "reason": "busy"}
    except (OSError, ValueError, RuntimeError):
        result = {"ok": False}
    try:
        send(connection, result)
    finally:
        connection.close()


def allocate(*, timeout, request_text=None):
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
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("plugin workspace allocation deadline exceeded")
    request = uuid.uuid4().hex
    result = run(_allocate_worker, (request, *arguments), timeout=remaining, label="plugin workspace allocation")
    if isinstance(result, dict) and set(result) == {"ok", "reason"} and result["ok"] is False:
        if result["reason"] == "capacity":
            raise WorkspaceAllocationFull("plugin workspace allocation unconfirmed; workspace capacity exhausted")
        if result["reason"] == "busy":
            raise WorkspaceAllocationBusy("plugin workspace allocation unconfirmed; workspace is busy")
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
    from core.plugin_workspace_recovery import cleanup_lease
    try:
        with cleanup_lease(root):
            _cleanup_worker_unlocked(connection, root, parent_identity, identity, request)
    except (OSError, ValueError, RuntimeError):
        from network.worker import send
        try:
            send(connection, {"ok": False})
        finally:
            connection.close()


def _cleanup_worker_unlocked(connection, root, parent_identity, identity, request):
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
def owned_workspace(*, timeout, cleanup_timeout, request_text=None):
    from core.paths import data_dir
    from core.plugin_disk_policy import check_space
    from core.plugin_workspace_budget import WORKSPACE_BYTES
    check_space(data_dir().absolute(), reserve_bytes=WORKSPACE_BYTES)
    from core.plugin_sandbox import WorkspaceError
    deadline = time.monotonic() + timeout
    try:
        attempted = set()
        busy_deadline = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("plugin workspace allocation deadline exceeded")
            try:
                workspace = allocate(timeout=remaining, request_text=request_text)
                break
            except WorkspaceAllocationFull:
                from core.plugin_workspace_recovery import recover_one_idle
                if not recover_one_idle(deadline=deadline, attempted=attempted):
                    raise
            except WorkspaceAllocationBusy:
                now = time.monotonic()
                if busy_deadline is None:
                    busy_deadline = min(deadline, now + BUSY_RETRY_SECONDS)
                if now >= busy_deadline:
                    raise
                time.sleep(min(.1, busy_deadline - now))
    except WorkspaceAllocationFull as exc:
        raise WorkspaceError("plugin workspace capacity exhausted", reason="capacity") from exc
    except WorkspaceAllocationBusy as exc:
        raise WorkspaceError("plugin workspace is busy", reason="busy") from exc
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        raise WorkspaceError("plugin workspace allocation failed; evidence retained") from exc
    from core.plugin_workspace_recovery import track
    with track(workspace):
        with _use_workspace(workspace, cleanup_timeout=cleanup_timeout):
            yield workspace


@contextmanager
def _use_workspace(workspace, *, cleanup_timeout):
    from core.plugin_sandbox import WorkspaceError
    from core.plugin_workspace_recovery import retire
    failure = None
    try:
        yield workspace
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            cleanup(workspace, timeout=cleanup_timeout)
            retire(workspace)
        except BaseException as exc:
            if failure is None:
                if isinstance(exc, Exception):
                    raise WorkspaceError("plugin workspace cleanup incomplete; evidence retained") from exc
                raise
            from core.cleanup import add_exception_note
            add_exception_note(failure, "plugin workspace cleanup incomplete; evidence retained")
