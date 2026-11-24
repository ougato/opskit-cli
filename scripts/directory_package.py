"""Build a deterministic full or incremental OpsKit directory release asset."""
from __future__ import annotations

import argparse
import hashlib
import io
import lzma
import os
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.directory_archive import (
    CHUNK_SIZE, MANIFEST_NAME, MAX_ARCHIVE_SIZE, MAX_TREE_SIZE, ArchiveError, DirectoryFile,
    archive_entry, file_mode, make_manifest, manifest_bytes, open_regular, read_manifest, tree_paths,
)
from core.paths import absolute_path


class _HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def _inventory(paths):
    total_size = 0
    for name, path in sorted(paths.items()):
        with open_regular(path) as (source, metadata):
            total_size += metadata.st_size
            if total_size > MAX_TREE_SIZE:
                raise ArchiveError("expanded package exceeds limit")
            stream = _HashingReader(source)
            remaining = metadata.st_size
            while remaining:
                data = stream.read(min(remaining, CHUNK_SIZE))
                if not data:
                    raise ArchiveError("package source changed during inventory")
                remaining -= len(data)
            if source.read(1):
                raise ArchiveError("package source changed during inventory")
            yield DirectoryFile(name, metadata.st_size, stream.digest.hexdigest(), file_mode(metadata))


def create_package(source: Path, output: Path, version: int, *, base: Path | None = None):
    """Build a full archive or a delta from a trusted full archive of the previous release."""
    source, output = absolute_path(source), absolute_path(output)
    if base is not None:
        base = absolute_path(base)
    if output.resolve().is_relative_to(source.resolve()):
        raise ArchiveError("output must be outside the package source")
    if base is not None and output.resolve() == base.resolve():
        raise ArchiveError("output must differ from the delta base")
    paths = tree_paths(source, materialize_links=True)
    previous = read_manifest(base) if base is not None else None
    if previous is not None and previous.base_version is not None:
        raise ArchiveError("delta base must be a full package")
    manifest = make_manifest(version, _inventory(paths), previous)
    changed = None if manifest.changed is None else set(manifest.changed)
    payload = manifest_bytes(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".opskit-package-", suffix=".tmp", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            with lzma.LZMAFile(destination, "wb", format=lzma.FORMAT_XZ, preset=6) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT, encoding="utf-8") as tar:
                    tar.addfile(archive_entry(MANIFEST_NAME, len(payload), 0o644), io.BytesIO(payload))
                    for file in manifest.files:
                        if changed is not None and file.path not in changed:
                            continue
                        with open_regular(paths[file.path]) as (source_file, metadata):
                            if metadata.st_size != file.size or file_mode(metadata) != file.mode:
                                raise ArchiveError("package source changed during archiving")
                            stream = _HashingReader(source_file)
                            try:
                                tar.addfile(archive_entry("payload/" + file.path, file.size, file.mode), stream)
                            except ValueError as error:
                                raise ArchiveError("unsupported archive path") from error
                            if stream.digest.hexdigest() != file.sha256 or source_file.read(1):
                                raise ArchiveError("package source changed during archiving")
            if destination.tell() > MAX_ARCHIVE_SIZE:
                raise ArchiveError("compressed package exceeds limit")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True, type=int)
    parser.add_argument("--base", type=Path)
    args = parser.parse_args()
    try:
        create_package(args.source, args.output, args.version, base=args.base)
    except (ArchiveError, OSError) as error:
        parser.exit(1, f"Directory package failed: {error}\n")


if __name__ == "__main__":
    main()
