"""Atomic pointers to immutable, independently verified native runtime objects."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import uuid
from contextlib import contextmanager, ExitStack

from core.native_runtime import stage
from core.native_runtime_prepare import validate_candidate
from core.resource_lock import ResourceLock

MAX_MANIFEST = 4 * 1024**2


class RuntimeSdkOutdated(ValueError):
    """A verified runtime targets an older host SDK."""


def _name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("invalid native runtime plugin identity")


def _read(path, limit):
    from core.plugin_runtime_inventory import _read_file
    from core.plugin_json import loads
    # Check the full ancestry rather than stopping at the object's parent.
    raw = _read_file(path, Path(path.anchor), limit)
    return loads(raw.decode("utf-8"))


class RuntimeCatalog:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self.lock = ResourceLock(lambda: self.root / ".catalog.lock", label="native runtime catalog")

    @contextmanager
    def acquire(self, name, *, fingerprint, sdk_sha256):
        """Pin the selected immutable object until its consumer finishes."""
        from core.native_catalog_leases import reader
        with ExitStack() as lease:
            with self.lock.hold():
                runtime, record = self.load(name, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
                lease.enter_context(reader(self.root, runtime.parent.name))
            yield runtime, record

    @contextmanager
    def acquire_for_sdk_refresh(self, name, *, fingerprint):
        """Pin and validate the existing runtime using its recorded SDK identity."""
        from core.native_catalog_leases import reader
        with ExitStack() as lease:
            with self.lock.hold():
                runtime, record = self._load(name, fingerprint=fingerprint, sdk_sha256=None)
                lease.enter_context(reader(self.root, runtime.parent.name))
            yield runtime, record

    def publish(self, name, candidate, record, *, check_identity):
        """Keep the old pointer unchanged until complete immutable data exists."""
        _name(name)
        validate_candidate(candidate, record, fingerprint=record["fingerprint"],
                           sdk_sha256=record["sdk_sha256"])
        content = json.dumps(record, sort_keys=True, separators=(",", ":"))
        if len(content.encode("utf-8")) > MAX_MANIFEST:
            raise ValueError("native runtime manifest exceeds limit")
        with self.lock.hold():
            if check_identity(name, record["fingerprint"]) is not True:
                raise PermissionError("native runtime plugin changed before publication")
            objects = self.root / "objects"
            objects.mkdir(exist_ok=True)
            # Unique objects are never overwritten; no reader can see half an update.
            identity = uuid.uuid4().hex
            object_root = objects / identity
            object_root.mkdir()
            from core.native_catalog_ownership import record_creation
            record_creation(self.root, identity)
            stage(candidate, object_root / "runtime", record["files"])
            from core.secrets import atomic_write
            atomic_write(object_root / "manifest.json", content)
            validate_candidate(object_root / "runtime", record, fingerprint=record["fingerprint"],
                               sdk_sha256=record["sdk_sha256"])
            if check_identity(name, record["fingerprint"]) is not True:
                raise PermissionError("native runtime plugin changed during publication")
            pointer = {"schema_version": 1, "object": identity,
                       "manifest_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
            atomic_write(self.root / (name + ".json"), json.dumps(pointer, sort_keys=True))
            return identity

    def is_current(self, name, runtime, record):
        """Check that a leased runtime still matches the published pointer."""
        _name(name)
        runtime = Path(runtime).absolute()
        identity = runtime.parent.name
        if (re.fullmatch(r"[0-9a-f]{32}", identity) is None
                or runtime != self.root / "objects" / identity / "runtime"):
            raise ValueError("invalid leased native runtime")
        content = json.dumps(record, sort_keys=True, separators=(",", ":"))
        expected = {"schema_version": 1, "object": identity,
                    "manifest_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        with self.lock.hold():
            try:
                pointer = _read(self.root / (name + ".json"), 4096)
            except FileNotFoundError:
                return False
        return (isinstance(pointer, dict) and type(pointer.get("schema_version")) is int
                and pointer == expected)

    def load(self, name, *, fingerprint, sdk_sha256):
        if not isinstance(sdk_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sdk_sha256) is None:
            raise ValueError("invalid expected native SDK identity")
        return self._load(name, fingerprint=fingerprint, sdk_sha256=sdk_sha256)

    def _load(self, name, *, fingerprint, sdk_sha256):
        _name(name)
        with self.lock.hold():
            pointer = _read(self.root / (name + ".json"), 4096)
            if (not isinstance(pointer, dict) or set(pointer) != {"schema_version", "object", "manifest_sha256"}
                    or type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1
                    or not isinstance(pointer["object"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", pointer["object"])
                    or not isinstance(pointer["manifest_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", pointer["manifest_sha256"])):
                raise ValueError("invalid native runtime pointer")
            object_root = self.root / "objects" / pointer["object"]
            record = _read(object_root / "manifest.json", MAX_MANIFEST)
            content = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != pointer["manifest_sha256"]:
                raise ValueError("native runtime manifest identity mismatch")
            if sdk_sha256 is None:
                if not isinstance(record, dict):
                    raise ValueError("invalid native runtime manifest")
                sdk_sha256 = record.get("sdk_sha256")
            elif (isinstance(record, dict) and isinstance(record.get("sdk_sha256"), str)
                    and record["sdk_sha256"] != sdk_sha256):
                # Only classify a stale SDK after verifying the original runtime
                # and plugin identity; corruption must never be treated as an update.
                validate_candidate(object_root / "runtime", record, fingerprint=fingerprint,
                                   sdk_sha256=record["sdk_sha256"])
                raise RuntimeSdkOutdated("native runtime SDK is outdated")
            validate_candidate(object_root / "runtime", record, fingerprint=fingerprint,
                               sdk_sha256=sdk_sha256)
            return object_root / "runtime", record
