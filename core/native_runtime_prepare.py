"""Assemble one private native runtime without importing supplied code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from core.native_runtime import inventory, stage, verify, absolute_path
from core.native_wheels import install
from core.plugin_lock import parse_lock


def assemble(source: Path, destination: Path, *, wheels, lock_text: str,
             sdk_files: dict[str, bytes], fingerprint: str,
             compatible_tags, marker_environment) -> dict:
    """Produce an unpublished candidate; caller owns atomic catalog publication.

    Interpreter tag/marker facts must be obtained by a trusted isolated probe.
    A candidate is not permission to run a plugin and does not confer trust.
    """
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("invalid plugin content identity")
    if not sdk_files or len(sdk_files) > 128 or sum(map(len, sdk_files.values())) > 16 * 1024**2:
        raise ValueError("invalid native SDK inventory")
    from pathlib import PurePosixPath
    for name, content in sdk_files.items():
        path = PurePosixPath(name)
        if (not isinstance(content, bytes) or len(path.parts) < 2 or path.parts[0] != "core"
                or path.is_absolute() or ".." in path.parts or "\\" in name
                or any(any(char in '<>:"|?*' or ord(char) < 32 for char in part)
                       or part.endswith((".", " ")) for part in path.parts)):
            raise ValueError("unsafe native SDK source path")
    folded_sdk = {name.casefold() for name in sdk_files}
    if len(folded_sdk) != len(sdk_files):
        raise ValueError("case-conflicting native SDK paths")
    for name in folded_sdk:
        if any(parent.as_posix() in folded_sdk for parent in PurePosixPath(name).parents):
            raise ValueError("native SDK file conflicts with directory")
    canonical_lock, _ = parse_lock(lock_text)
    destination = absolute_path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("runtime candidate already exists")
    source_files = inventory(source)
    destination.mkdir()
    stage(source, destination / "python", source_files)
    site = destination / "site-packages"
    selected = install(wheels, canonical_lock, site, compatible_tags=compatible_tags,
                       marker_environment=marker_environment)
    # SDK code must not be replaced by a dependency distribution.
    if any(path.name.casefold() == "core" for path in site.iterdir()):
        raise ValueError("dependency conflicts with platform SDK")
    sdk_digest = hashlib.sha256()
    for name, content in sorted(sdk_files.items()):
        target = site.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(content)
        sdk_digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(content).digest())
    files = inventory(destination)
    record = {"schema_version": 1, "backend": "native", "fingerprint": fingerprint,
              "lock_sha256": hashlib.sha256(canonical_lock.encode("utf-8")).hexdigest(),
              "sdk_sha256": sdk_digest.hexdigest(), "wheels": selected, "files": files}
    # Exercise wire-size bounds before returning data intended for publication.
    if len(json.dumps(record).encode("utf-8")) > 4 * 1024**2:
        raise ValueError("native runtime manifest exceeds limit")
    verify(destination, record["files"])
    return record


def _validate_identity(record, *, fingerprint, sdk_sha256):
    if (not isinstance(record, dict) or set(record) != {
            "schema_version", "backend", "fingerprint", "lock_sha256", "sdk_sha256", "wheels", "files"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["backend"] != "native" or record["fingerprint"] != fingerprint
            or record["sdk_sha256"] != sdk_sha256
            or not all(isinstance(record[key], str) and re.fullmatch(r"[0-9a-f]{64}", record[key])
                       for key in ("fingerprint", "lock_sha256", "sdk_sha256"))
            or not isinstance(record["wheels"], dict) or not record["wheels"]
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in record["wheels"].values())):
        raise ValueError("native runtime identity is stale or invalid")


def validate_candidate(root, record, *, fingerprint, sdk_sha256):
    """Reject stale plugin/SDK identities and mutated runtime bytes before use."""
    _validate_identity(record, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
    verify(root, record["files"])


def stage_candidate(root, destination, record, *, fingerprint, sdk_sha256):
    """Check identity and create a verified private snapshot without duplicate scans."""
    _validate_identity(record, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
    stage(root, destination, record["files"])
