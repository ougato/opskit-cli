"""Inspect and extract a signed-release directory package into a private empty tree."""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import re
import stat
import tarfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from core.paths import absolute_path

MANIFEST_NAME = ".opskit-manifest.json"
MAX_ARCHIVE_SIZE = 256 * 1024 * 1024
MAX_TREE_SIZE = 512 * 1024 * 1024
MAX_MANIFEST_SIZE = 2 * 1024 * 1024
MAX_FILES = 4096
MAX_VERSION = 2**31 - 1
CHUNK_SIZE = 128 * 1024
_XZ_MEMORY_LIMIT = 128 * 1024 * 1024
_WINDOWS_RESERVED = {
    "AUX", "CON", "NUL", "PRN", "CONIN$", "CONOUT$",
    *(prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"),
}


class ArchiveError(ValueError):
    """Invalid package, filesystem tree or transition base."""


@dataclass(frozen=True)
class DirectoryFile:
    path: str
    size: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class DirectoryManifest:
    version: int
    tree_sha256: str
    files: tuple[DirectoryFile, ...]
    base_version: int | None = None
    base_tree_sha256: str | None = None
    changed: tuple[str, ...] | None = None


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _file_records(files):
    return [{"path": file.path, "size": file.size, "sha256": file.sha256, "mode": file.mode} for file in files]


def _tree_hash(files) -> str:
    return hashlib.sha256(_json(_file_records(files))).hexdigest()


def manifest_bytes(manifest: DirectoryManifest) -> bytes:
    base = None
    if manifest.base_version is not None:
        base = {"version": manifest.base_version, "tree_sha256": manifest.base_tree_sha256,
                "changed": manifest.changed}
    return _json({"schema": 1, "version": manifest.version, "files": _file_records(manifest.files),
                  "tree_sha256": manifest.tree_sha256, "base": base})


def validate_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path
        or re.search(r"[\ud800-\udfff]", path)
        or len(path.encode("utf-8")) > 230
    ):
        raise ArchiveError("invalid package path")
    parts = path.split("/")
    if len(parts) > 16 or parts[0].casefold() == MANIFEST_NAME:
        raise ArchiveError("invalid package path")
    for part in parts:
        if (
            not part
            or len(part.encode("utf-8")) > 100
            or part.endswith((".", " "))
            or re.search(r'[\x00-\x1f\x7f<>:"\\|?*]', part)
            or part.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED
        ):
            raise ArchiveError("invalid package path")


def _valid_version(value) -> bool:
    return type(value) is int and 0 < value <= MAX_VERSION


def _valid_digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _parse_manifest(payload: bytes) -> DirectoryManifest:
    try:
        data = json.loads(payload.decode("utf-8"))
        return _decode_manifest(data, payload)
    except ArchiveError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ArchiveError("invalid package manifest") from error


def _decode_manifest(data, payload: bytes) -> DirectoryManifest:
    if (
        not isinstance(data, dict)
        or data.keys() != {"schema", "version", "files", "tree_sha256", "base"}
        or type(data["schema"]) is not int
        or data["schema"] != 1
        or not _valid_version(data["version"])
        or not _valid_digest(data["tree_sha256"])
        or not isinstance(data["files"], list)
        or not 0 < len(data["files"]) <= MAX_FILES
    ):
        raise ArchiveError("invalid package manifest")
    files = []
    prefixes = {}
    file_paths = set()
    total_size = 0
    for record in data["files"]:
        if (
            not isinstance(record, dict)
            or record.keys() != {"path", "size", "sha256", "mode"}
            or type(record["mode"]) is not int
            or record["mode"] not in {0o644, 0o755}
            or type(record["size"]) is not int
            or record["size"] < 0
            or not _valid_digest(record["sha256"])
        ):
            raise ArchiveError("invalid package file")
        validate_path(record["path"])
        file = DirectoryFile(**record)
        if files and files[-1].path >= file.path:
            raise ArchiveError("package paths must be unique and ordered")
        segments = file.path.split("/")
        for index in range(len(segments)):
            prefix = "/".join(segments[:index + 1])
            identity = unicodedata.normalize("NFC", prefix).casefold()
            if prefixes.setdefault(identity, prefix) != prefix or identity in file_paths:
                raise ArchiveError("package paths collide")
        file_paths.add(identity)
        total_size += file.size
        if total_size > MAX_TREE_SIZE:
            raise ArchiveError("expanded package exceeds limit")
        files.append(file)
    if _tree_hash(files) != data["tree_sha256"]:
        raise ArchiveError("package tree digest mismatch")
    base = data["base"]
    if base is None:
        result = DirectoryManifest(data["version"], data["tree_sha256"], tuple(files))
    else:
        if (
            not isinstance(base, dict)
            or base.keys() != {"version", "tree_sha256", "changed"}
            or not _valid_version(base["version"])
            or base["version"] >= data["version"]
            or not _valid_digest(base["tree_sha256"])
            or not isinstance(base["changed"], list)
            or any(not isinstance(path, str) for path in base["changed"])
            or base["changed"] != sorted(set(base["changed"]))
        ):
            raise ArchiveError("invalid package base")
        if not set(base["changed"]).issubset(file.path for file in files):
            raise ArchiveError("invalid delta payload")
        result = DirectoryManifest(data["version"], data["tree_sha256"], tuple(files),
                                   base["version"], base["tree_sha256"], tuple(base["changed"]))
    if manifest_bytes(result) != payload:
        raise ArchiveError("package manifest is not canonical")
    return result


def make_manifest(version: int, files, base: DirectoryManifest | None = None) -> DirectoryManifest:
    """Build and validate a manifest from an ordered file inventory."""
    files = tuple(files)
    tree_hash = _tree_hash(files)
    if base is None:
        manifest = DirectoryManifest(version, tree_hash, files)
    else:
        previous = {file.path: file for file in base.files}
        changed = tuple(file.path for file in files if previous.get(file.path) != file)
        manifest = DirectoryManifest(version, tree_hash, files, base.version, base.tree_sha256, changed)
    payload = manifest_bytes(manifest)
    if len(payload) > MAX_MANIFEST_SIZE:
        raise ArchiveError("package manifest exceeds limit")
    return _parse_manifest(payload)


def archive_entry(name: str, size: int, mode: int) -> tarfile.TarInfo:
    result = tarfile.TarInfo(name)
    result.size = size
    result.mode = mode
    return result


class _XzReader:
    def __init__(self, stream):
        self.stream = stream
        self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=_XZ_MEMORY_LIMIT)
        self.position = 0

    def read(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size and not self.decoder.eof:
            compressed = b""
            if self.decoder.needs_input:
                compressed = self.stream.read(CHUNK_SIZE)
                if not compressed:
                    raise ArchiveError("truncated compressed package")
            chunks.extend(self.decoder.decompress(compressed, max_length=size - len(chunks)))
        self.position += len(chunks)
        return bytes(chunks)

    def finish(self) -> None:
        if self.read(1) or self.decoder.unused_data or self.stream.read(1):
            raise ArchiveError("unexpected trailing package data")


def _read_exact(stream: _XzReader, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ArchiveError("truncated package")
    return data


def _check_zeros(stream: _XzReader, size: int) -> None:
    if any(_read_exact(stream, size)):
        raise ArchiveError("invalid package padding")


def _check_header(raw: bytes, name: str, size: int, mode: int) -> None:
    try:
        expected = archive_entry(name, size, mode).tobuf(format=tarfile.USTAR_FORMAT, encoding="utf-8")
    except ValueError as error:
        raise ArchiveError("unsupported archive path") from error
    if raw != expected:
        raise ArchiveError("unexpected archive entry")


def _read_manifest(stream: _XzReader) -> DirectoryManifest:
    raw = _read_exact(stream, tarfile.BLOCKSIZE)
    try:
        entry = tarfile.TarInfo.frombuf(raw, encoding="utf-8", errors="strict")
    except (tarfile.HeaderError, UnicodeError) as error:
        raise ArchiveError("invalid package manifest header") from error
    if not 0 < entry.size <= MAX_MANIFEST_SIZE:
        raise ArchiveError("invalid package manifest size")
    _check_header(raw, MANIFEST_NAME, entry.size, 0o644)
    manifest = _parse_manifest(_read_exact(stream, entry.size))
    _check_zeros(stream, -entry.size % tarfile.BLOCKSIZE)
    return manifest


def is_link(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@contextmanager
def open_regular(path: Path):
    path = absolute_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or is_link(before):
        raise ArchiveError("unsafe package file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not os.path.samestat(before, metadata):
            raise ArchiveError("package file changed while opening")
        yield stream, metadata


@contextmanager
def _open_archive(archive: Path):
    try:
        with open_regular(archive) as (stream, metadata):
            if not 0 < metadata.st_size <= MAX_ARCHIVE_SIZE:
                raise ArchiveError("invalid compressed package size")
            yield _XzReader(stream)
    except lzma.LZMAError as error:
        raise ArchiveError("invalid xz package compression") from error


def read_manifest(archive: Path) -> DirectoryManifest:
    """Read and validate only the bounded manifest at the front of an archive."""
    with _open_archive(archive) as stream:
        return _read_manifest(stream)


def tree_paths(root: Path, *, installed: bool = False, materialize_links: bool = False):
    """Inventory files without descending through symbolic links or reparse points."""
    root = absolute_path(root)
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or is_link(metadata):
        raise ArchiveError("unsafe directory package root")
    root = absolute_path(root.resolve(strict=True))
    files = {}
    directories = [(root, "")]
    directory_count = 0
    while directories:
        directory, prefix = directories.pop()
        for child in directory.iterdir():
            name = prefix + child.name
            if name == MANIFEST_NAME and installed:
                continue
            validate_path(name)
            metadata = child.lstat()
            if is_link(metadata):
                if not materialize_links or not child.is_symlink() or os.path.isabs(os.readlink(child)):
                    raise ArchiveError("package tree contains a link")
                child = absolute_path(child.resolve(strict=True))
                if not child.is_relative_to(root) or not stat.S_ISREG(child.stat().st_mode):
                    raise ArchiveError("package link must point to a regular file inside the tree")
            elif stat.S_ISDIR(metadata.st_mode):
                directory_count += 1
                if directory_count > MAX_FILES:
                    raise ArchiveError("package tree exceeds directory count limit")
                directories.append((child, name + "/"))
                continue
            elif not stat.S_ISREG(metadata.st_mode):
                raise ArchiveError("package tree contains a special file")
            files[name] = child
            if len(files) > MAX_FILES:
                raise ArchiveError("package tree exceeds file count limit")
    return files


def file_mode(metadata) -> int:
    return 0o755 if metadata.st_mode & 0o111 else 0o644


def _read_installed(base: Path, *, version: int, tree_sha256: str):
    paths = tree_paths(base, installed=True)
    with open_regular(base / MANIFEST_NAME) as (stream, metadata):
        if not 0 < metadata.st_size <= MAX_MANIFEST_SIZE:
            raise ArchiveError("invalid installed manifest size")
        previous = _parse_manifest(stream.read(MAX_MANIFEST_SIZE + 1))
    if (
        previous.base_version is not None
        or previous.version != version
        or previous.tree_sha256 != tree_sha256
    ):
        raise ArchiveError("delta base identity mismatch")
    if paths.keys() != {file.path for file in previous.files}:
        raise ArchiveError("delta base file inventory mismatch")
    for file in previous.files:
        with open_regular(paths[file.path]) as (stream, metadata):
            _check_local_file(metadata, file)
            _copy_file(stream, file)
    return {file.path: file for file in previous.files}, paths


def verify_installed_tree(path: Path, *, version: int, tree_sha256: str) -> None:
    """Revalidate a staged tree against the identity retained at extraction."""
    _read_installed(path, version=version, tree_sha256=tree_sha256)


def _check_local_file(metadata, file: DirectoryFile) -> None:
    if (
        metadata.st_nlink != 1
        or metadata.st_size != file.size
        or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != file.mode)
    ):
        raise ArchiveError("delta base file metadata mismatch")


def _copy_file(stream, file: DirectoryFile, destination=None) -> None:
    digest = hashlib.sha256()
    remaining = file.size
    while remaining:
        data = stream.read(min(remaining, CHUNK_SIZE))
        if not data:
            raise ArchiveError("truncated package file")
        digest.update(data)
        if destination is not None:
            destination.write(data)
        remaining -= len(data)
    if digest.hexdigest() != file.sha256:
        raise ArchiveError("package file digest mismatch")


def _write_file(path: Path, stream, file: DirectoryFile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        _copy_file(stream, file, output)
        if os.name != "nt":
            os.fchmod(output.fileno(), file.mode)
        output.flush()
        os.fsync(output.fileno())


def _sync_tree(destination: Path) -> None:
    if os.name != "nt":
        for directory, _subdirectories, _files in os.walk(destination, topdown=False):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if directory != str(destination):
                    os.fchmod(descriptor, 0o755)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def unpack_package(archive: Path, destination: Path, version: int, *, base: Path | None = None) -> DirectoryManifest:
    """Verify and materialize a full or delta package in an empty private staging directory."""
    destination = absolute_path(destination)
    metadata = destination.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or is_link(metadata) or any(destination.iterdir()):
        raise ArchiveError("package destination must be an empty directory")
    with _open_archive(archive) as stream:
        manifest = _read_manifest(stream)
        if not _valid_version(version) or manifest.version != version:
            raise ArchiveError("package version mismatch")
        if manifest.changed is None:
            changed = {file.path for file in manifest.files}
            previous, old_paths = {}, {}
        else:
            if base is None:
                raise ArchiveError("delta requires an installed base")
            previous, old_paths = _read_installed(
                base, version=manifest.base_version, tree_sha256=manifest.base_tree_sha256,
            )
            expected = tuple(file.path for file in manifest.files if previous.get(file.path) != file)
            if manifest.changed != expected:
                raise ArchiveError("delta payload does not match its base")
            changed = set(manifest.changed)
        for file in manifest.files:
            target = destination / file.path
            if file.path in changed:
                raw = _read_exact(stream, tarfile.BLOCKSIZE)
                _check_header(raw, "payload/" + file.path, file.size, file.mode)
                _write_file(target, stream, file)
                _check_zeros(stream, -file.size % tarfile.BLOCKSIZE)
            else:
                with open_regular(old_paths[file.path]) as (old_file, old_metadata):
                    _check_local_file(old_metadata, file)
                    _write_file(target, old_file, file)
        _check_zeros(stream, 2 * tarfile.BLOCKSIZE)
        _check_zeros(stream, -stream.position % tarfile.RECORDSIZE)
        stream.finish()
    installed = DirectoryManifest(manifest.version, manifest.tree_sha256, manifest.files)
    with (destination / MANIFEST_NAME).open("xb") as output:
        output.write(manifest_bytes(installed))
        output.flush()
        os.fsync(output.fileno())
    _sync_tree(destination)
    return manifest
