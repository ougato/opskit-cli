"""Durable, leased intents for exclusively owned Docker resources."""
import json
from itertools import islice
import os
import re
import stat
import subprocess
import uuid

from core.paths import data_dir
from core.resource_lock import ResourceLock, ResourceBusyError
from core.secrets import atomic_write

MAX_RECORDS = 256
MAX_RECORD_BYTES = 4096
LABEL = "opskit.cleanup-token"


def _root():
    root = data_dir() / "plugin-resources"
    for path in (root, *root.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("resource journal must not contain links")
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


def _read(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RECORD_BYTES:
        raise ValueError("invalid resource journal file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        current = os.fstat(stream.fileno())
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("resource journal changed during opening")
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("resource journal exceeds size limit")
    record = json.loads(raw)
    if not isinstance(record, dict) or set(record) != {"schema", "token", "kind", "name", "backend", "daemon"}:
        raise ValueError("invalid resource journal schema")
    if (type(record["schema"]) is not int or record["schema"] != 1
            or not isinstance(record["token"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["token"])
            or path.name != record["token"] + ".json"
            or record["kind"] not in ("container", "network")
            or not isinstance(record["name"], str)
            or not re.fullmatch(r"opskit-(?:net-)?[0-9a-f]{32}(?:-proxy)?", record["name"])
            or not isinstance(record["backend"], list)
            or not isinstance(record["daemon"], str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", record["daemon"])):
        raise ValueError("invalid resource journal identity")
    _backend(record["backend"])
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
    if _read(path) != record:
        raise ValueError("resource journal changed during cleanup")
    path.unlink()


class ResourceLease:
    """Keep the OS lease open through launch, execution and cleanup."""
    def __init__(self, command, kind, name, control):
        self.command = _backend(command)
        self.root = _root()
        if kind not in ("container", "network") or not isinstance(name, str) or not re.fullmatch(r"opskit-(?:net-)?[0-9a-f]{32}(?:-proxy)?", name):
            raise ValueError("invalid managed resource identity")
        token = uuid.uuid4().hex
        self.path = self.root / (token + ".json")
        self.record = {"schema": 1, "token": token, "kind": kind, "name": name,
                       "backend": self.command, "daemon": _daemon(control)}
        self.lock = ResourceLock(lambda: self.root / (token + ".lock"), label="plugin resource").hold()
        self.lock.__enter__()
        try:
            with ResourceLock(lambda: self.root / ".index.lock", label="plugin resource index").hold():
                if len(list(islice(self.root.glob("*.json"), MAX_RECORDS))) >= MAX_RECORDS:
                    raise RuntimeError("resource journal is full; reconcile before execution")
                atomic_write(self.path, json.dumps(self.record))
                _read(self.path)
        except BaseException:
            self.lock.__exit__(None, None, None)
            if not os.path.lexists(self.path):
                try:
                    self.path.with_suffix(".lock").unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        self.closed = False

    @property
    def labels(self):
        return ["--label", LABEL + "=" + self.record["token"]]

    def close(self, control):
        if self.closed:
            raise RuntimeError("resource lease already closed")
        try:
            _cleanup(self.path, self.record, self.command, control)
        finally:
            self.closed = True
            self.lock.__exit__(None, None, None)
            if not os.path.lexists(self.path):
                try:
                    self.path.with_suffix(".lock").unlink(missing_ok=True)
                except OSError:
                    pass  # A concurrent observer may still hold the retired lease.


def reconcile(command, control):
    """Skip live leases and retain uncertain resources; never execute stored commands."""
    root = _root()
    paths = sorted(islice(root.glob("*.json"), MAX_RECORDS + 1))
    if len(paths) > MAX_RECORDS:
        raise RuntimeError("resource journal exceeds recovery limit")
    result = {"recovered": 0, "active": 0, "pending": 0, "invalid": 0}
    for path in paths:
        if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
            result["invalid"] += 1
            continue
        lock = ResourceLock(lambda path=path: path.with_suffix(".lock"), label="plugin resource")
        try:
            with lock.hold():
                if not os.path.lexists(path):
                    continue
                record = _read(path)
                _cleanup(path, record, command, control)
        except ResourceBusyError:
            result["active"] += 1
        except (ValueError, OSError, json.JSONDecodeError):
            result["invalid"] += 1
        except (RuntimeError, subprocess.SubprocessError):
            result["pending"] += 1
        else:
            result["recovered"] += 1
            try:
                path.with_suffix(".lock").unlink(missing_ok=True)
            except OSError:
                pass
    return result
