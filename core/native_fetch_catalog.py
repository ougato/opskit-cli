"""Prepared platform-owned native Git toolchains, never selected by plugins."""
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid
import zlib
from contextlib import contextmanager, ExitStack

from core.native_runtime import absolute_path, inventory, stage, verify
from core.native_sandbox import NativeSandboxError, get_backend
from core.plugin_runtime_inventory import _read_file, code_digest, native_fetch_snapshot
from core.resource_lock import ResourceLock

MAX_MANIFEST = 4 * 1024**2


def _probe_repository(root):
    """Known object graph written without running any host executable."""
    root.mkdir()
    (root / "objects").mkdir()
    (root / "refs" / "heads").mkdir(parents=True)
    def obj(kind, body):
        content = kind.encode() + b" " + str(len(body)).encode() + b"\0" + body
        oid = hashlib.sha1(content).hexdigest()  # Git object identity, not a trust signature.
        parent = root / "objects" / oid[:2]
        parent.mkdir(exist_ok=True)
        (parent / oid[2:]).write_bytes(zlib.compress(content))
        return oid
    blob = obj("blob", b"native-fetch-probe\n")
    tree = obj("tree", b"100644 probe.txt\0" + bytes.fromhex(blob))
    commit = obj("commit", ("tree " + tree + "\nauthor OpsKit <probe@invalid> 0 +0000\n"
                            "committer OpsKit <probe@invalid> 0 +0000\n\nnative probe\n").encode())
    (root / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
    (root / "refs" / "heads" / "main").write_text(commit + "\n", encoding="ascii")
    (root / "config").write_text("[core]\n repositoryformatversion = 0\n bare = true\n", encoding="ascii")
    return commit


def _read_json(path, limit):
    from core.plugin_json import loads
    return loads(_read_file(path, Path(path.anchor), limit).decode("utf-8"))


def _record(runtime):
    return {"schema_version": 1, "platform": sys.platform,
            "code_sha256": code_digest(native_fetch_snapshot()), "files": inventory(runtime)}


def _validate(runtime, record):
    if (not isinstance(record, dict) or set(record) != {"schema_version", "platform", "code_sha256", "files"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["platform"] != sys.platform
            or record["code_sha256"] != code_digest(native_fetch_snapshot())):
        raise ValueError("native fetch toolchain policy changed")
    verify(runtime, record["files"])
    if "python/python.exe" not in record["files"]:
        raise ValueError("native fetch Python executable missing")


class FetchCatalog:
    def __init__(self, root=None):
        if root is None:
            from core.paths import data_dir
            root = data_dir() / "native-fetch-tools"
        self.root = absolute_path(root)
        self.lock = ResourceLock(lambda: self.root / ".catalog.lock", label="native fetch tools")

    def prepare(self, python, git_runtime=None, *, timeout=300):
        """Snapshot an explicit Python runtime, prove acquisition, then publish it.

        No source Git, pip, package scripts or plugin code executes on the host.
        Preparation does not authorize installation or grant plugin permissions.
        """
        if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
            raise ValueError("invalid native fetch preparation budget")
        deadline = time.monotonic() + timeout
        def check():
            if time.monotonic() >= deadline:
                raise NativeSandboxError("execution_timeout")
        with self.lock.hold():
            # Take a private immutable snapshot before executing the probe; later
            # source edits cannot change what is tested and published.
            from core.native_preparation import workspace
            with workspace() as temporary:
                base = absolute_path(temporary)
                candidate = base / "runtime"
                candidate.mkdir()
                stage(python, candidate / "python", inventory(python))
                check()
                record = _record(candidate)
                _validate(candidate, record)
                source = base / "probe-repository"
                expected_commit = _probe_repository(source)
                from core.native_fetch import local_candidate
                with local_candidate(candidate / "python", None, source,
                                     staging_parent=base, deadline=deadline, checkpoint=check,
                                     branch="main") as (result, _):
                    if (result.reported_commit != expected_commit or len(result.files) != 1
                            or result.files[0].path != "probe.txt"
                            or result.files[0].sha256 != hashlib.sha256(b"native-fetch-probe\n").hexdigest()):
                        raise ValueError("native fetch preparation probe mismatch")
                check()
                _validate(candidate, record)
                content = json.dumps(record, sort_keys=True, separators=(",", ":"))
                if len(content.encode()) > MAX_MANIFEST:
                    raise ValueError("native fetch manifest exceeds limit")
                objects = self.root / "objects"
                objects.mkdir(exist_ok=True)
                identity = uuid.uuid4().hex
                owned = objects / identity
                owned.mkdir()
                from core.native_catalog_ownership import record_creation
                record_creation(self.root, identity)
                stage(candidate, owned / "runtime", record["files"])
                from core.secrets import atomic_write
                atomic_write(owned / "manifest.json", content)
                _validate(owned / "runtime", record)
                check()
                pointer = {"schema_version": 1, "object": identity,
                           "manifest_sha256": hashlib.sha256(content.encode()).hexdigest()}
                atomic_write(self.root / "current.json", json.dumps(pointer, sort_keys=True))
                return identity

    def load(self):
        """Validate full prepared content every time; never discover ambient Git."""
        get_backend().probe()
        with self.lock.hold():
            pointer = _read_json(self.root / "current.json", 4096)
            if (not isinstance(pointer, dict) or set(pointer) != {"schema_version", "object", "manifest_sha256"}
                    or type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1
                    or not isinstance(pointer["object"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", pointer["object"])
                    or not isinstance(pointer["manifest_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", pointer["manifest_sha256"])):
                raise ValueError("invalid native fetch tool pointer")
            obj = self.root / "objects" / pointer["object"]
            record = _read_json(obj / "manifest.json", MAX_MANIFEST)
            content = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(content.encode()).hexdigest() != pointer["manifest_sha256"]:
                raise ValueError("native fetch manifest changed")
            _validate(obj / "runtime", record)
            return obj / "runtime", record

    @contextmanager
    def acquire(self):
        from core.native_catalog_leases import reader
        with ExitStack() as lease:
            with self.lock.hold():
                runtime, record = self.load()
                lease.enter_context(reader(self.root, runtime.parent.name))
            yield runtime, record

    @contextmanager
    def local_candidate(self, source, *, staging_parent, deadline, checkpoint, branch=None, pack=None):
        """Carry prepared file identities through snapshot creation, not just load."""
        from core.native_fetch import local_candidate
        with self.acquire() as (runtime, record):
            tools = {"python": {path[len("python") + 1:]: digest for path, digest in record["files"].items()
                                if path.startswith("python/")}}
            with local_candidate(runtime / "python", None, source, staging_parent=staging_parent,
                                 deadline=deadline, checkpoint=checkpoint, branch=branch, tool_files=tools, pack=pack) as result:
                yield result
