"""Host-owned, plugin-partitioned output receipts and explicit safe collection."""
from contextlib import contextmanager
import hashlib
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import uuid

from core.paths import data_dir
from core.plugin_state import _directory, _remove_owned_tree
from core.resource_lock import ResourceLock
from core.plugin_host_tools import _ordinary, _grant, MAX_OUTPUT_BYTES, MAX_OUTPUT_DEPTH, MAX_OUTPUT_ENTRIES
from core.plugin_runtime_inventory import _read_file
from core.secrets import atomic_write


def _root(plugin):
    if not isinstance(plugin, str) or re.fullmatch(r"[a-z][a-z0-9_]*", plugin) is None:
        raise ValueError("invalid receipt owner")
    return data_dir().absolute() / "plugin-artifacts" / plugin


def _record_path(plugin, token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid output token")
    return _root(plugin) / (token + ".json")


@contextmanager
def resource_lock(resource):
    identity = _directory(_ordinary(str(Path(resource).absolute()), directory=True))
    key = hashlib.sha256(json.dumps(list(identity), separators=(",", ":")).encode()).hexdigest()
    with ResourceLock(lambda: data_dir() / "plugin-artifact-locks" / (key + ".lock"), label="host resource").hold():
        yield


def _snapshot(path, checkpoint, expected_files=None, *, file_hashes=None):
    from core.plugin_tree import bounded_paths
    from core.host_file_access import open_bounded
    path = _ordinary(str(path), directory=True)
    identity = _directory(path)
    digest = hashlib.sha256()
    expected_files = expected_files or {}
    found = set()
    size = files = 0
    paths = list(bounded_paths(path, max_entries=MAX_OUTPUT_ENTRIES, max_depth=64,
                              skip_dirs=frozenset(), checkpoint=checkpoint))
    for child in sorted(paths):
        checkpoint()
        info = child.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
            raise ValueError("unsafe output link")
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            files += 1
            size += info.st_size
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("unsafe output entry")
        if size > MAX_OUTPUT_BYTES:
            raise ValueError("output exceeds limit")
        if stat.S_ISREG(info.st_mode):
            relative_name = child.relative_to(path).as_posix()
            if file_hashes is not None and (len(file_hashes) >= 128 or len(relative_name) > 512):
                file_hashes.clear()
                file_hashes = None
            file_digest = hashlib.sha256() if relative_name in expected_files or file_hashes is not None else None
            with open_bounded(child, path, MAX_OUTPUT_BYTES) as source:
                while body := source.read(1024**2):
                    checkpoint()
                    digest.update(body)
                    if file_digest is not None:
                        file_digest.update(body)
            if relative_name in expected_files:
                if file_digest.hexdigest() != expected_files[relative_name]:
                    raise ValueError("output file hash does not match validated content")
                found.add(relative_name)
            if file_hashes is not None:
                file_hashes[relative_name] = file_digest.hexdigest()
        digest.update(json.dumps([child.relative_to(path).as_posix(), info.st_dev, info.st_ino,
            info.st_mode, info.st_size, info.st_mtime_ns], separators=(",", ":")).encode())
    if _directory(path) != identity:
        raise ValueError("output replaced")
    if found != set(expected_files):
        raise ValueError("validated output file missing")
    return {"identity": list(identity), "parent_identity": list(_directory(path.parent)),
            "inventory_sha256": digest.hexdigest(), "bytes": size, "files": files}


def _mkdir(path, plugin, fingerprint, *, authority, check_identity, checkpoint):
    if (not path.is_absolute() or path.name in {"", ".", ".."}
            or any(ord(c) < 32 for c in str(path))):
        raise ValueError("ordinary absolute directory required")
    parent = _ordinary(str(path.parent), directory=True)
    identity = _directory(parent)
    scope = _grant(plugin, fingerprint, "directory.write", str(path), authority, check_identity, checkpoint)
    with resource_lock(parent):
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        if _directory(_ordinary(str(parent), directory=True)) != identity:
            raise ValueError("directory parent changed")
        if not os.path.lexists(path):
            path.mkdir()
        return _ordinary(str(path), directory=True)


def _publish_tree(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    """Publish a digest-bound copy to a new directory on the destination volume."""
    from io import BytesIO

    from core.host_file_access import open_bounded, relative_host_path, source_path
    from core.native_task_control import periodic_checkpoint
    from core.plugin_tree import bounded_paths

    if (os.name != "nt" or set(parameters) - {"create_parents_from"} != {"source", "destination", "files"}
            or not isinstance(parameters["destination"], str)):
        raise ValueError("invalid tree publication request")
    source = _ordinary(parameters["source"], directory=True)
    destination = Path(parameters["destination"])
    if "create_parents_from" in parameters:
        root = _ordinary(parameters["create_parents_from"], directory=True)
        parts = destination.relative_to(root).parts
        if (not 1 <= len(parts) <= 16 or any(
                part in {".", ".."} or part.endswith((".", " ")) or ":" in part
                or any(ord(char) < 32 for char in part) for part in parts)
                or destination == source):
            raise ValueError("invalid tree publication path")
        parent = root
        for part in parts[:-1]:
            child = parent / part
            try:
                parent = _ordinary(str(child), directory=True)
            except FileNotFoundError:
                parent = _mkdir(child, plugin, fingerprint, authority=authority,
                                check_identity=check_identity, checkpoint=checkpoint)
    parent = _ordinary(str(destination.parent), directory=True)
    _, destination, identity = source_path(str(parent), destination.name)
    if destination == source:
        raise ValueError("destination overlaps source")
    files = parameters["files"]
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_OUTPUT_ENTRIES:
        raise ValueError("invalid tree inventory")
    validated = {}
    inline_limit = 4 * 1024**2 + 1024
    inline_size = 0
    for name, entry in files.items():
        if not isinstance(entry, dict) or set(entry) not in ({"source", "sha256"}, {"content", "sha256"}):
            raise ValueError("invalid tree entry")
        target = relative_host_path(parent, name)
        digest = entry["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid tree digest")
        if "source" in entry:
            original = relative_host_path(source, entry["source"])
            if original == destination or destination in original.parents:
                raise ValueError("destination overlaps source")
        else:
            content = entry["content"]
            if not isinstance(content, str) or len(content) > ((4 * 1024**2 + 2) // 3) * 4:
                raise ValueError("invalid inline tree content")
            original = base64.b64decode(content, validate=True)
            inline_size += len(original)
            if inline_size > inline_limit or hashlib.sha256(original).hexdigest() != digest:
                raise ValueError("invalid inline tree digest or size")
        validated[target.relative_to(parent).as_posix()] = (original, digest)
    if len(validated) != len(files) or len({name.casefold() for name in validated}) != len(files):
        raise ValueError("duplicate tree entry")
    read_scope = _grant(plugin, fingerprint, "directory.read", str(source), authority, check_identity, checkpoint)
    write_scope = _grant(plugin, fingerprint, "directory.read_write", str(parent), authority, check_identity, checkpoint)
    source_identity = _directory(source)

    def check_location():
        if _directory(source) != source_identity or source_path(str(parent), destination.name)[2] != identity:
            raise ValueError("tree publication location changed")

    def check():
        checkpoint()
        authority.check_many((read_scope, write_scope), check_identity=check_identity)
        check_location()

    expected = {name: digest for name, (_, digest) in validated.items()}
    poll = periodic_checkpoint(check)
    with resource_lock(parent):
        check()
        if os.path.lexists(destination):
            if _snapshot(destination, poll, expected)["files"] != len(expected):
                raise ValueError("existing tree differs from verified source")
            check()
            return {"directory": str(destination)}
        staging = parent / (".opskit-tree-" + uuid.uuid4().hex)
        staging.mkdir()
        staging_identity = tuple(_directory(staging))
        total = 0
        written = {}
        try:
            for name, (original, expected_digest) in validated.items():
                poll()
                check_location()
                temporary = staging.joinpath(*PurePosixPath(name).parts)
                temporary.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                incoming = BytesIO(original) if isinstance(original, bytes) else open_bounded(
                    original, source, MAX_OUTPUT_BYTES)
                with incoming as stream, temporary.open("xb") as outgoing:
                    length = 0
                    while body := stream.read(1024**2):
                        poll()
                        total += len(body)
                        if total > MAX_OUTPUT_BYTES:
                            raise ValueError("tree exceeds limit")
                        digest.update(body)
                        if outgoing.write(body) != len(body):
                            raise OSError("short write during tree publication")
                        length += len(body)
                    outgoing.flush()
                    info = os.fstat(outgoing.fileno())
                    if info.st_size != length:
                        raise OSError("staged file size differs from bytes written")
                    written[name] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                if digest.hexdigest() != expected_digest:
                    raise ValueError("tree source changed during copy")
            check()
            if tuple(_directory(staging)) != staging_identity:
                raise ValueError("staging directory changed")
            for entry in bounded_paths(staging, max_entries=MAX_OUTPUT_ENTRIES, max_depth=MAX_OUTPUT_DEPTH,
                                       skip_dirs=frozenset(), checkpoint=poll):
                info = entry.lstat()
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("unsafe staging entry")
                if stat.S_ISDIR(info.st_mode):
                    continue
                identity_and_size = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or written.pop(entry.relative_to(staging).as_posix(), None) != identity_and_size):
                    raise ValueError("staged tree differs from verified source")
            if written or tuple(_directory(staging)) != staging_identity:
                raise ValueError("staged tree differs from verified source")
            check()
            os.rename(staging, destination)
            return {"directory": str(destination)}
        finally:
            if staging.exists():
                _remove_owned_tree(staging, staging_identity, lambda: None, max_entries=MAX_OUTPUT_ENTRIES,
                                   max_files=MAX_OUTPUT_ENTRIES, max_bytes=MAX_OUTPUT_BYTES, max_depth=64)


def _zip_entry(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    from stat import S_ISLNK
    from zipfile import ZipFile
    from core.host_file_access import MAX_ARTIFACT_BYTES, open_bounded, source_path

    fields = set(parameters)
    if fields not in ({"directory", "file", "member"}, {"directory", "file", "member_pattern"}):
        raise ValueError("invalid ZIP entry request")
    exact = "member" in parameters
    selector = parameters["member" if exact else "member_pattern"]
    if not isinstance(selector, str):
        raise ValueError("invalid ZIP entry selector")
    if exact:
        def matches_name(name):
            return name == selector
    else:
        if selector.count("*") != 1 or not re.fullmatch(r"[A-Za-z0-9_./*-]{1,256}", selector):
            raise ValueError("invalid ZIP entry pattern")
        prefix, suffix = selector.split("*")
        pattern = re.compile(re.escape(prefix) + r"[^/\\]+" + re.escape(suffix))

        def matches_name(name):
            return pattern.fullmatch(name) is not None
    directory, archive, identity = source_path(parameters["directory"], parameters["file"])
    scope = _grant(plugin, fingerprint, "directory.read", str(directory), authority, check_identity, checkpoint)
    if source_path(parameters["directory"], parameters["file"])[2] != identity:
        raise ValueError("ZIP directory changed")
    limit = 1024 * 1024
    with open_bounded(archive, directory, MAX_ARTIFACT_BYTES) as stream, ZipFile(stream) as package:
        matches = [item for item in package.infolist() if matches_name(item.filename)]
        if (len(matches) != 1 or matches[0].is_dir() or S_ISLNK(matches[0].external_attr >> 16)
                or matches[0].file_size > limit):
            raise ValueError("ZIP entry missing, ambiguous or too large")
        with package.open(matches[0]) as member:
            body = member.read(limit + 1)
    if len(body) > limit or source_path(parameters["directory"], parameters["file"])[2] != identity:
        raise ValueError("ZIP entry changed or too large")
    checkpoint()
    authority.check(scope, check_identity=check_identity)
    return {"content": base64.b64encode(body).decode("ascii")}


def _zip_inventory(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    from stat import S_ISLNK
    from zipfile import ZipFile
    from core.host_file_access import MAX_ARTIFACT_BYTES, open_bounded, source_path

    if set(parameters) != {"directory", "file", "sha256", "prefix"}:
        raise ValueError("invalid ZIP inventory request")
    expected, prefix = parameters["sha256"], parameters["prefix"]
    if (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not isinstance(prefix, str) or not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)+", prefix)):
        raise ValueError("invalid ZIP inventory bounds")
    directory, archive, identity = source_path(parameters["directory"], parameters["file"])
    scope = _grant(plugin, fingerprint, "directory.read", str(directory), authority, check_identity, checkpoint)
    if source_path(parameters["directory"], parameters["file"])[2] != identity:
        raise ValueError("ZIP directory changed")

    def digest(stream):
        value = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024**2):
            checkpoint()
            value.update(chunk)
            size += len(chunk)
        return {"size": size, "sha256": value.hexdigest()}

    files = {}
    with open_bounded(archive, directory, MAX_ARTIFACT_BYTES) as stream, ZipFile(stream) as package:
        stream.seek(0)
        if digest(stream)["sha256"] != expected:
            raise ValueError("ZIP archive digest differs")
        members = [item for item in package.infolist()
                   if item.filename.startswith(prefix) and not item.is_dir()]
        if len(members) > 10_000 or sum(item.file_size for item in members) > 2 * 1024**3:
            raise ValueError("ZIP inventory exceeds limits")
        for item in members:
            name = item.filename[len(prefix):]
            if (not name or name in files or "\\" in name or ":" in name
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or S_ISLNK(item.external_attr >> 16)):
                raise ValueError("invalid ZIP inventory entry")
            with package.open(item) as member:
                files[name] = digest(member)
        stream.seek(0)
        if digest(stream)["sha256"] != expected:
            raise ValueError("ZIP archive changed")
    checkpoint()
    authority.check(scope, check_identity=check_identity)
    return files


def _read_verified_file(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    from core.host_file_access import open_bounded, source_path

    if set(parameters) != {"directory", "file", "sha256", "limit"}:
        raise ValueError("invalid verified file request")
    limit, expected = parameters["limit"], parameters["sha256"]
    if (type(limit) is not int or not 1 <= limit <= 8 * 1024**2
            or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("invalid verified file bounds")
    directory, path, identity = source_path(parameters["directory"], parameters["file"])
    scope = _grant(plugin, fingerprint, "directory.read", str(directory), authority, check_identity, checkpoint)
    if source_path(parameters["directory"], parameters["file"])[2] != identity:
        raise ValueError("verified file directory changed")
    with open_bounded(path, directory, limit) as stream:
        body = stream.read(limit + 1)
    if len(body) > limit or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("verified file digest or size differs")
    checkpoint()
    authority.check(scope, check_identity=check_identity)
    return {"content": base64.b64encode(body).decode("ascii")}


def _file_header(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    from core.host_file_access import MAX_ARTIFACT_BYTES, open_bounded, source_path

    if set(parameters) != {"directory", "file"}:
        raise ValueError("invalid file header request")
    directory, path, identity = source_path(parameters["directory"], parameters["file"])
    scope = _grant(plugin, fingerprint, "directory.read", str(directory), authority, check_identity, checkpoint)
    if source_path(parameters["directory"], parameters["file"])[2] != identity:
        raise ValueError("file header directory changed")
    with open_bounded(path, directory, MAX_ARTIFACT_BYTES) as stream:
        header = stream.read(12)
        digest = hashlib.sha256(header)
        while chunk := stream.read(1024**2):
            checkpoint()
            digest.update(chunk)
    checkpoint()
    authority.check(scope, check_identity=check_identity)
    return {"header": header.hex(), "sha256": digest.hexdigest()}


def _remove_verified_file(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    from core.host_file_access import open_bounded, source_path

    if os.name != "nt" or set(parameters) != {"directory", "file", "sha256"}:
        raise ValueError("invalid verified file removal")
    directory, name, digest = (parameters[key] for key in ("directory", "file", "sha256"))
    if (not isinstance(name, str) or "/" in name or "\\" in name
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("invalid verified file removal")
    root, target, identity = source_path(directory, name)
    scope = _grant(plugin, fingerprint, "directory.read_write", str(root),
                   authority, check_identity, checkpoint)

    def check():
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        if source_path(directory, name) != (root, target, identity):
            raise ValueError("verified removal directory changed")

    def inspect(path):
        with open_bounded(path, root, 1024**2) as stream:
            info = os.fstat(stream.fileno())
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("file differs from expected content")
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    with resource_lock(root):
        check()
        before = inspect(target)
        retired = root / (".opskit-removed-" + uuid.uuid4().hex)
        check()
        os.rename(target, retired)
        try:
            if inspect(retired) != before:
                raise ValueError("file changed during removal")
            check()
            final = retired.lstat()
            if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != before or final.st_nlink != 1:
                raise ValueError("file changed before removal")
            retired.unlink()
        except BaseException:
            if not os.path.lexists(target) and source_path(directory, name) == (root, target, identity):
                os.rename(retired, target)
            raise
    return {"removed": True}


def _sign_update_manifest(plugin, fingerprint, parameters, *, authority, check_identity, checkpoint):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from core.host_file_access import open_bounded, source_path

    required = {"directory", "file", "manifest", "public_key"}
    if set(parameters) not in (required, required | {"signature"}):
        raise ValueError("invalid update signing request")
    encoded, public = parameters["manifest"], parameters["public_key"]
    limit = 4 * 1024**2
    if (not isinstance(encoded, str) or len(encoded) > ((limit + 2) // 3) * 4
            or not isinstance(public, str) or len(public) > 256):
        raise ValueError("invalid update signing input")
    body = base64.b64decode(encoded, validate=True)
    manifest = json.loads(body)
    if (len(body) > limit or not isinstance(manifest, dict)
            or type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 2
            or not isinstance(manifest.get("files"), list)):
        raise ValueError("invalid update manifest")
    expected_key = base64.b64decode(public, validate=True)
    directory, key_path, identity = source_path(parameters["directory"], parameters["file"])
    scope = _grant(plugin, fingerprint, "directory.read", str(directory), authority, check_identity, checkpoint)
    if source_path(parameters["directory"], parameters["file"])[2] != identity:
        raise ValueError("signing key directory changed")
    with open_bounded(key_path, directory, 16384) as stream:
        try:
            key = serialization.load_pem_private_key(stream.read(16385), password=None)
        except (ValueError, TypeError):
            raise ValueError("invalid update signing key") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("update signing requires a P-256 key")
    verifier = key.public_key()
    if verifier.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != expected_key:
        raise ValueError("signing key does not match the build public key")
    checkpoint()
    authority.check(scope, check_identity=check_identity)
    message = b"game-update-manifest-v2\0" + body
    algorithm = ec.ECDSA(hashes.SHA256())
    if "signature" in parameters:
        encoded_signature = parameters["signature"]
        if not isinstance(encoded_signature, str) or not 1 <= len(encoded_signature) <= 108:
            raise ValueError("invalid existing update signature")
        signature = base64.b64decode(encoded_signature, validate=True)
        if not 1 <= len(signature) <= 80:
            raise ValueError("invalid existing update signature")
    else:
        signature = key.sign(message, ec.ECDSA(hashes.SHA256(), deterministic_signing=True))
    try:
        verifier.verify(signature, message, algorithm)
    except InvalidSignature:
        raise ValueError("invalid update manifest signature") from None
    return {"signature": base64.b64encode(signature).decode("ascii")}


def pending(plugin, token, resource, path, checkpoint):
    checkpoint()
    record = _record_path(plugin, token)
    if os.path.lexists(record):
        raise ValueError("output token already exists")
    value = {"schema_version": 1, "owner": plugin, "resource": str(resource), "directory": str(path),
             "phase": "pending", "completed_ns": time.time_ns(),
             "identity": list(_directory(path)), "parent_identity": list(_directory(path.parent))}
    _save(plugin, token, value)


def _save(plugin, token, value):
    record = _record_path(plugin, token)
    for parent in record.parents:
        if os.path.lexists(parent):
            _directory(parent)
    record.parent.mkdir(parents=True, exist_ok=True)
    from core.secrets import restrict_permissions
    restrict_permissions(record.parent)
    _ordinary(str(record.parent), directory=True)
    atomic_write(record, json.dumps(value, sort_keys=True))


def _load(plugin, token):
    path = _record_path(plugin, token)
    raw = _read_file(path, Path(path.anchor), 16384)
    value = json.loads(raw)
    base = {"schema_version", "owner", "resource", "directory", "phase", "completed_ns", "identity", "parent_identity"}
    if (not isinstance(value, dict) or value.get("phase") not in {"pending", "committed"}
            or set(value) != (base if value["phase"] == "pending" else base | {"inventory_sha256", "bytes", "files"})
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["owner"] != plugin
            or type(value["completed_ns"]) is not int
            or not all(isinstance(value[k], str) and Path(value[k]).is_absolute() for k in ("resource", "directory"))
            or not all(isinstance(value[k], list) and len(value[k]) == 2
                       and all(type(n) is int for n in value[k]) for k in ("identity", "parent_identity"))):
        raise ValueError("invalid output receipt")
    if value["phase"] == "committed" and (
            type(value["bytes"]) is not int or not 0 <= value["bytes"] <= MAX_OUTPUT_BYTES
            or type(value["files"]) is not int or not 0 <= value["files"] <= MAX_OUTPUT_ENTRIES
            or not isinstance(value["inventory_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["inventory_sha256"]) is None):
        raise ValueError("invalid output snapshot")
    return value, hashlib.sha256(raw).hexdigest()


def _matches(value, snapshot):
    return all(value.get(key) == observed for key, observed in snapshot.items())


def _required(path, names, checkpoint):
    from core.host_file_access import open_bounded
    if not isinstance(names, list) or len(names) > 32:
        raise ValueError("invalid required output files")
    for name in names:
        if not isinstance(name, str) or "\\" in name:
            raise ValueError("invalid required output path")
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or any(p in {"..", "."} or ":" in p for p in relative.parts):
            raise ValueError("invalid required output path")
        checkpoint()
        with open_bounded(path.joinpath(*relative.parts), path, MAX_OUTPUT_BYTES) as source:
            if not source.read(1):
                raise ValueError("required output is empty")


def manage(plugin, fingerprint, action, parameters, *, authority, check_identity, deadline, checkpoint, **unused):
    from core.native_task_control import periodic_checkpoint
    if not isinstance(parameters, dict):
        raise ValueError("invalid artifact parameters")
    if action == "publish_tree":
        return _publish_tree(plugin, fingerprint, parameters, authority=authority,
                             check_identity=check_identity, checkpoint=checkpoint)
    if action == "zip_entry":
        return _zip_entry(plugin, fingerprint, parameters, authority=authority,
                          check_identity=check_identity, checkpoint=checkpoint)
    if action == "zip_inventory":
        return _zip_inventory(plugin, fingerprint, parameters, authority=authority,
                              check_identity=check_identity, checkpoint=checkpoint)
    if action == "sign_update_manifest":
        return _sign_update_manifest(plugin, fingerprint, parameters, authority=authority,
                                     check_identity=check_identity, checkpoint=checkpoint)
    if action == "file_header":
        return _file_header(plugin, fingerprint, parameters, authority=authority,
                            check_identity=check_identity, checkpoint=checkpoint)
    if action == "read_verified_file":
        return _read_verified_file(plugin, fingerprint, parameters, authority=authority,
                                   check_identity=check_identity, checkpoint=checkpoint)
    if action == "remove_verified_file":
        return _remove_verified_file(plugin, fingerprint, parameters, authority=authority,
                                     check_identity=check_identity, checkpoint=checkpoint)
    if action == "mkdir":
        if set(parameters) != {"directory"} or not isinstance(parameters["directory"], str):
            raise ValueError("invalid directory request")
        directory = _mkdir(Path(parameters["directory"]), plugin, fingerprint, authority=authority,
                           check_identity=check_identity, checkpoint=checkpoint)
        return {"directory": str(directory)}
    allowed = {"list": {"resource", "keep"}, "commit": {"id", "required_files"},
               "clean": {"id", "record_sha256", "keep"},
               "adopt": {"resource", "directory", "snapshot", "required_files", "completed_ns"}}
    optional = {"expected_files", "include_file_hashes"} if action == "commit" else set()
    if action not in allowed or not allowed[action] <= set(parameters) or set(parameters) - allowed[action] - optional:
        raise ValueError("invalid artifact operation")
    expected_files = parameters.get("expected_files", {})
    include_file_hashes = parameters.get("include_file_hashes", False)
    if type(include_file_hashes) is not bool:
        raise ValueError("invalid file hash request")
    if (not isinstance(expected_files, dict) or len(expected_files) > 32
            or any(not isinstance(name, str) or not name or "\\" in name
                   or PurePosixPath(name).is_absolute() or any(part in {"..", "."} or ":" in part for part in PurePosixPath(name).parts)
                   or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for name, digest in expected_files.items())):
        raise ValueError("invalid expected output file hashes")
    initial_deadline = deadline() if callable(deadline) else deadline
    inspection_deadline = min(initial_deadline, time.monotonic() + 30)
    def check():
        checkpoint()
        current_deadline = deadline() if callable(deadline) else deadline
        effective_deadline = min(current_deadline, inspection_deadline + max(0, current_deadline - initial_deadline))
        if time.monotonic() >= effective_deadline:
            raise TimeoutError("artifact operation deadline exceeded")
    if action == "list":
        resource = _ordinary(parameters["resource"], directory=True)
    elif action == "adopt":
        resource = _ordinary(parameters["resource"], directory=True)
    else:
        value, digest = _load(plugin, parameters["id"])
        resource = _ordinary(value["resource"], directory=True)
    keep = parameters.get("keep", 1)
    if type(keep) is not int or not 1 <= keep <= 100:
        raise ValueError("invalid output retention")
    with resource_lock(resource):
        check()
        if action == "list":
            scope = _grant(plugin, fingerprint, "directory.read", str(resource), authority, check_identity, check)
            def live():
                check()
                authority.check(scope, check_identity=check_identity)
            live()
            rows = _inspect(plugin, resource, keep, periodic_checkpoint(live))
            live()
            return rows
        if action == "adopt":
            if type(parameters["completed_ns"]) is not int or not 0 < parameters["completed_ns"] <= time.time_ns():
                raise ValueError("invalid receipt timestamp")
            path = _ordinary(parameters["directory"], directory=True)
        else:
            # Reload after acquiring the shared build/cleanup lock.
            value, digest = _load(plugin, parameters["id"])
            path = _ordinary(value["directory"], directory=True)
        if action == "commit":
            # An execution-created pending output has a host-chosen random name.
            # Authorize its stable parent, while continuing to verify the owned
            # token and both directory identities before recording a receipt.
            if value["phase"] != "pending" or path.name != "opskit-" + parameters["id"]:
                raise ValueError("invalid pending output")
            permission_path = _ordinary(str(path.parent), directory=True)
        else:
            # Adoption and especially deletion retain exact-output consent.
            permission_path = path
        scope = _grant(plugin, fingerprint, "directory.write", str(permission_path), authority, check_identity, check)
        scopes = (scope,)
        if include_file_hashes:
            scopes += (_grant(plugin, fingerprint, "directory.read", str(path), authority, check_identity, check),)
        def live():
            check()
            authority.check_many(scopes, check_identity=check_identity)
        live()
        poll = periodic_checkpoint(live)
        file_hashes = {} if include_file_hashes else None
        if file_hashes is not None:
            snapshot = _snapshot(path, poll, expected_files, file_hashes=file_hashes)
        else:
            snapshot = _snapshot(path, poll, expected_files) if expected_files else _snapshot(path, poll)
        if action == "adopt":
            expected = parameters["snapshot"]
            # Import never trusts caller-provided content hashes without recomputing them.
            if not isinstance(expected, dict) or not all(expected.get(k) == snapshot[k] for k in (
                    "identity", "parent_identity", "inventory_sha256", "bytes")):
                raise ValueError("legacy output no longer matches its receipt")
            _required(path, parameters["required_files"], live)
            for row in _inspect(plugin, resource, 1, poll):
                if row["directory"] == str(path):
                    live()
                    return row
            token = uuid.uuid4().hex
            pending(plugin, token, resource, path, live)
            value, digest = _load(plugin, token)
        else:
            token = parameters["id"]
        if action in {"commit", "adopt"}:
            if value["phase"] != "pending" or snapshot["identity"] != value["identity"] or snapshot["parent_identity"] != value["parent_identity"]:
                raise ValueError("pending output changed")
            if snapshot["files"] == 0 or snapshot["bytes"] == 0:
                raise ValueError("empty output")
            _required(path, parameters["required_files"], live)
            value.update(snapshot, phase="committed", completed_ns=parameters.get("completed_ns", time.time_ns()))
            live()
            _save(plugin, token, value)
            result = {"id": token, "directory": str(path), "bytes": snapshot["bytes"], "files": snapshot["files"]}
            if file_hashes:
                result["file_hashes"] = file_hashes
            return result
        rows = _inspect(plugin, resource, keep, poll)
        row = next((row for row in rows if row["id"] == token), None)
        if (not row or not row["cleanup_candidate"] or digest != parameters["record_sha256"]
                or not _matches(value, snapshot)):
            raise ValueError("output changed, uncommitted, or retained")
        if _snapshot(path, poll) != snapshot:
            raise ValueError("output changed after candidate inspection")
        def deleting():
            live()
            if _load(plugin, token)[1] != digest or list(_directory(path.parent)) != snapshot["parent_identity"]:
                raise ValueError("output receipt or parent changed")
        _remove_owned_tree(path, tuple(snapshot["identity"]), deleting,
                           max_entries=MAX_OUTPUT_ENTRIES, max_files=MAX_OUTPUT_ENTRIES,
                           max_bytes=MAX_OUTPUT_BYTES, max_depth=64)
        _record_path(plugin, token).unlink()
        return {"removed": token}


def _inspect(plugin, resource, keep, checkpoint):
    root = _root(plugin)
    if not root.exists():
        return []
    _ordinary(str(root), directory=True)
    rows, total = [], 0
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            checkpoint()
            if index >= 4096:
                raise ValueError("receipt inventory limit")
            value, digest = _load(plugin, entry.name.removesuffix(".json"))
            if value["resource"] != str(resource) or value["phase"] != "committed":
                continue
            total += value["bytes"]
            if len(rows) >= 128 or total > 8 * 1024**3:
                raise ValueError("receipt inspection budget exceeded")
            try:
                valid = _matches(value, _snapshot(Path(value["directory"]), checkpoint))
            except TimeoutError:
                raise
            except (OSError, ValueError):
                valid = False
            rows.append({"id": entry.name[:-5], "directory": value["directory"], "completed_ns": value["completed_ns"],
                         "bytes": value["bytes"], "valid": valid, "record_sha256": digest})
    rows.sort(key=lambda row: (row["completed_ns"], row["id"]), reverse=True)
    for index, row in enumerate(rows):
        row["cleanup_candidate"] = index >= keep and row["valid"]
    return rows
