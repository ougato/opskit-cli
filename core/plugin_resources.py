"""Durable, leased intents for exclusively owned Docker resources."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

from core.paths import data_dir
from core.resource_lock import ResourceBusyError
from core.plugin_json import loads as load_json

MAX_RECORDS = 256
MAX_RECORD_BYTES = 4096
LABEL = "opskit.cleanup-token"


def _root(*, create=True):
    root = (data_dir() / "plugin-resources").absolute()
    for path in (root, *root.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("resource journal must not contain links")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _backend(command):
    if not isinstance(command, list):
        raise ValueError("invalid Docker resource backend")
    if command == ["docker"]:
        return list(command)
    if (len(command) == 5 and command[:2] == ["wsl", "-d"]
            and command[3:] == ["--exec", "docker"]
            and isinstance(command[2], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", command[2])):
        return list(command)
    raise ValueError("unsupported Docker resource backend")


def _daemon(control):
    value = control("info", "--format", "{{.ID}}")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", value):
        raise ValueError("invalid Docker daemon identity")
    return value


def _journal_version(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_ino <= 0
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not 0 <= info.st_size <= MAX_RECORD_BYTES):
        raise ValueError("invalid resource journal file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read(path):
    version = _journal_version(path.lstat())
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if _journal_version(os.fstat(stream.fileno())) != version:
            raise ValueError("resource journal changed during opening")
        raw = stream.read(MAX_RECORD_BYTES + 1)
        if (len(raw) != version[2] or _journal_version(os.fstat(stream.fileno())) != version
                or _journal_version(path.lstat()) != version):
            raise ValueError("resource journal changed during reading")
    record = load_json(raw.decode("utf-8"))
    return _record_value(path, record)


def _record_value(path, record):
    if not isinstance(record, dict):
        raise ValueError("invalid resource journal schema")
    fields = {"schema", "token", "kind", "name", "backend", "daemon"}
    schema = record.get("schema")
    extra = {2: {'workspace'}, 3: {'workspace_intent'}}.get(schema, set()) if type(schema) is int else set()
    if type(schema) is not int or schema not in (1, 2, 3) or set(record) != fields | extra:
        raise ValueError("invalid resource journal schema")
    if (not isinstance(record["token"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", record["token"])
            or path.name != record["token"] + ".json"
            or record["kind"] not in ("container", "network")
            or not isinstance(record["name"], str)
            or not re.fullmatch(r"opskit-(?:net-)?[0-9a-f]{32}(?:-proxy)?", record["name"])
            or not isinstance(record["backend"], list)
            or not isinstance(record["daemon"], str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", record["daemon"])):
        raise ValueError("invalid resource journal identity")
    _backend(record["backend"])
    if schema == 2:
        from core.plugin_workspace import recorded_workspace
        if record["kind"] != "container":
            raise ValueError("only containers can own a workspace")
        recorded_workspace(record["workspace"])
    elif schema == 3:
        from core.plugin_workspace import recorded_workspace_intent
        if record['kind'] != 'container':
            raise ValueError('only containers can request a workspace')
        recorded_workspace_intent(record['workspace_intent'])
    return record


@contextmanager
def _inspection_reader():
    from core.plugin_inspection import ContentInspector
    from core import plugin_sandbox as sandbox
    scoped = getattr(sandbox._invocations, "inspection_scope", False)
    reader = getattr(sandbox._invocations, "inspector", None) if scoped else None
    if reader is None:
        reader = ContentInspector()
        if scoped:
            sandbox._invocations.inspector = reader
    try:
        yield reader
    finally:
        if not scoped:
            reader.close()


def _read_supervised(path, *, inspector=None):
    from core import plugin_sandbox as sandbox
    if inspector is None:
        with _inspection_reader() as reader:
            return _read_supervised(path, inspector=reader)
    record = inspector.resource_record(path, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))
    sandbox._remaining()
    return record


def _find(record, control):
    arguments = [record["kind"], "ls"]
    if record["kind"] == "container":
        arguments.append("-a")
    arguments += ["--no-trunc", "--filter", "label=" + LABEL + "=" + record["token"],
                  "--filter", "name=" + record["name"], "--format", "{{json .}}"]
    response = control(*arguments)
    if not isinstance(response, str):
        raise ValueError("invalid resource listing")
    lines = response.splitlines()
    if len(lines) > 1:
        raise ValueError("ambiguous owned resource")
    if not lines:
        return None
    value = json.loads(lines[0])
    if (not isinstance(value, dict) or value.get("Names" if record["kind"] == "container" else "Name") != record["name"]
            or not isinstance(value.get("ID"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["ID"])):
        raise ValueError("invalid owned resource response")
    return value["ID"]


def _cleanup(path, record, command, control):
    if record["backend"] != _backend(command) or _daemon(control) != record["daemon"]:
        raise RuntimeError("Docker backend changed; resource retained")
    identity = _find(record, control)
    if identity:
        arguments = ("rm", "-f", identity) if record["kind"] == "container" else ("network", "rm", identity)
        try:
            control(*arguments)
        except (RuntimeError, subprocess.SubprocessError):
            # Docker --rm can retire the object between listing and deletion.
            # A failed deletion is safe only after the same daemon confirms
            # that the exact labelled resource is absent. Keep uncertain intents.
            if _daemon(control) != record["daemon"] or _find(record, control) is not None:
                raise
        if _find(record, control) is not None:
            raise RuntimeError("owned resource remains after cleanup")
    if _daemon(control) != record["daemon"]:
        raise RuntimeError("Docker backend changed; resource retained")
    if record['schema'] == 3:
        from core.plugin_resource_io import retire_absent_intent
        from core import plugin_sandbox as sandbox
        retire_absent_intent(path, record, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))
        sandbox._remaining()
        return
    if record["schema"] == 2:
        from core.plugin_workspace import cleanup, recorded_workspace
        from core import plugin_sandbox as sandbox
        cleanup(recorded_workspace(record["workspace"]), timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))
    from core.plugin_resource_io import remove_record
    from core import plugin_sandbox as sandbox
    remove_record(path, record, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))
    sandbox._remaining()


def _lease_lock(path=None, *, token=None):
    from core.plugin_invocation_lock import hold
    from core import plugin_sandbox as sandbox
    return hold(path, resource=token, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))


def _retire_metadata(path):
    from core.plugin_resource_io import retire_lock
    from core import plugin_sandbox as sandbox
    try:
        return retire_lock(path, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))
    except (OSError, ValueError, RuntimeError):
        # Metadata is optional to retire; never replace the business failure.
        # Unknown/busy metadata stays for a later recovery operation.
        return 'unconfirmed'


class ResourceLease:
    """Keep the OS lease open through launch, execution and cleanup."""
    def __init__(self, command, kind, name, control, *, workspace=None):
        self.command = _backend(command)
        if kind not in ("container", "network") or not isinstance(name, str) or not re.fullmatch(r"opskit-(?:net-)?[0-9a-f]{32}(?:-proxy)?", name):
            raise ValueError("invalid managed resource identity")
        token = uuid.uuid4().hex
        self.record = {"schema": 1, "token": token, "kind": kind, "name": name,
                       "backend": self.command, "daemon": _daemon(control)}
        if workspace is not None:
            from core.plugin_workspace import journal_binding
            self.record.update(schema=2, workspace=journal_binding(workspace))
            _record_value(Path(token + '.json'), self.record)
        self.lock = _lease_lock(token=token)
        lock_path = self.lock.__enter__()
        self.root = lock_path.parent
        self.path = lock_path.with_suffix('.json')
        try:
            from core.plugin_resource_io import create_record
            from core import plugin_sandbox as sandbox
            create_record(self.path, self.record, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT),
                          limit=MAX_RECORDS)
            sandbox._remaining()
        except BaseException:
            self.lock.__exit__(None, None, None)
            _retire_metadata(self.path)
            raise
        self.closed = False
        self.workspace_requested = workspace is not None

    @property
    def labels(self):
        return ["--label", LABEL + "=" + self.record["token"]]

    def allocate_workspace(self, *, timeout, request_text=None):
        from core.plugin_workspace import allocate
        if self.closed or self.workspace_requested or self.record['kind'] != 'container':
            raise ValueError('workspace already requested or lease unavailable')
        self.workspace_requested = True
        return allocate(timeout=timeout, request_text=request_text, resource_record=self.record)

    def close(self, control):
        if self.closed:
            raise RuntimeError("resource lease already closed")
        try:
            current = _read_supervised(self.path)
            if self.record['schema'] == 1 and current['schema'] in (2, 3):
                original = {key: value for key, value in current.items()
                            if key not in ('workspace', 'workspace_intent')}
                original['schema'] = 1
            else:
                original = current
            if original != self.record:
                raise ValueError('resource binding changed; evidence retained')
            _cleanup(self.path, current, self.command, control)
        finally:
            self.closed = True
            self.lock.__exit__(None, None, None)
            _retire_metadata(self.path)


def reconcile(command, control):
    """Skip live leases and retain uncertain resources; never execute stored commands."""
    with _inspection_reader() as inspector:
        return _reconcile(command, control, inspector)


def _reconcile(command, control, inspector):
    from core.plugin_resource_io import inventory
    from core import plugin_sandbox as sandbox
    root, names, invalid = inventory(timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT), limit=MAX_RECORDS)
    sandbox._remaining()
    paths = [root / name for name in names]
    result = {"recovered": 0, "active": 0, "pending": 0, "invalid": invalid}
    for path in paths:
        try:
            with _lease_lock(path.with_suffix(".lock")):
                record = _read_supervised(path, inspector=inspector)
                _cleanup(path, record, command, control)
        except FileNotFoundError:
            _retire_metadata(path)
            continue
        except ResourceBusyError:
            result["active"] += 1
        except TimeoutError:
            result["pending"] += 1
        except (ValueError, OSError, json.JSONDecodeError):
            result["invalid"] += 1
        except (RuntimeError, subprocess.SubprocessError):
            result["pending"] += 1
        else:
            result["recovered"] += 1
            _retire_metadata(path)
    return result
