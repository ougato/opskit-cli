"""Bounded archive extraction with explicit, validated internal tar links."""
from __future__ import annotations

import shutil
import os
import posixpath
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_EXTRACT_BYTES = 2 * 1024**3
MAX_EXTRACT_FILES = 100_000
MAX_ARCHIVE_PATH_BYTES = 4096
MAX_ARCHIVE_PATH_DEPTH = 128
MAX_LAYOUT_NODES = 200_000
MAX_LAYOUT_BYTES = 64 * 1024**2


class _LayoutBudget:
    """Charge distinct explicit/implicit paths before building layout graphs."""
    def __init__(self, archive=None):
        self.node_limit = min(MAX_LAYOUT_NODES, getattr(archive, "file_limit", MAX_EXTRACT_FILES) * 2)
        self.byte_limit = min(MAX_LAYOUT_BYTES, getattr(archive, "metadata_limit", MAX_LAYOUT_BYTES))
        self.names = set()
        self.bytes = 0

    def add(self, name, spelling=None):
        if name in self.names:
            return
        cost = 128 + len(name.encode("utf-8", "surrogatepass"))
        if spelling is not None:
            cost += len(spelling.encode("utf-8", "surrogatepass"))
        if len(self.names) >= self.node_limit or self.bytes + cost > self.byte_limit:
            raise ValueError("archive layout exceeds limits")
        self.names.add(name)
        self.bytes += cost


def _unsafe_windows_component(part: str) -> bool:
    stem = part.split(".", 1)[0].upper()
    devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    return (part.endswith((".", " ")) or any(c in '<>"|?*' or ord(c) < 32 for c in part)
            or stem in devices
            or len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³")


def _target(root: Path, name: str, *, utf8_paths=False) -> Path:
    if len(name.encode("utf-8")) > MAX_ARCHIVE_PATH_BYTES:
        raise ValueError("archive path exceeds limits")
    path = PurePosixPath(name.replace("\\", "/"))
    if len(path.parts) > MAX_ARCHIVE_PATH_DEPTH:
        raise ValueError("archive path exceeds limits")
    if ("\0" in name or path.is_absolute() or ".." in path.parts
            or any(":" in p for p in path.parts)
            or os.name == "nt" and any(_unsafe_windows_component(p) for p in path.parts)):
        raise ValueError("unsafe archive path")
    target = root.joinpath(*path.parts)
    if utf8_paths:
        target = Path(os.path.realpath(str(target).encode("utf-8")).decode("utf-8"))
    else:
        target = target.resolve()
    target.relative_to(root)
    return target


def _tar_links(selected, *, archive=None):
    entries = {}
    directories = set()
    budget = _LayoutBudget(archive)
    for member, name in selected:
        if name in entries:
            raise ValueError("duplicate archive member")
        budget.add(name)
        entries[name] = member
        for parent in PurePosixPath(name).parents:
            directory = str(parent)
            if directory != ".":
                budget.add(directory)
                directories.add(directory)
    for name in directories:
        if name in entries and not entries[name].isdir():
            raise ValueError("archive writes beneath a file or link")
    links = {}
    for name, member in entries.items():
        if not member.issym():
            continue
        raw = member.linkname.replace("\\", "/")
        if not raw or raw.startswith("/") or any(":" in part for part in raw.split("/")):
            raise ValueError("unsafe archive link")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), raw))
        if target == ".." or target.startswith("../") or target == ".":
            raise ValueError("unsafe archive link")
        for parent in PurePosixPath(target).parents:
            if str(parent) in entries and not entries[str(parent)].isdir():
                raise ValueError("archive link traverses a file or link")
        links[name] = target
    resolved = {}
    for name in links:
        trail, seen, current = [], set(), name
        while current in links and current not in resolved:
            if current in seen:
                raise ValueError("cyclic archive link")
            seen.add(current)
            trail.append(current)
            current = links[current]
        terminal = resolved.get(current, current)
        if terminal not in entries and terminal not in directories:
            raise ValueError("archive link target is missing")
        for node in trail:
            resolved[node] = terminal
    validated = [(name, target, resolved[name] in directories or entries[resolved[name]].isdir())
                 for name, target in links.items()]
    # Directory links must not create recursive traversal, including indirect cycles.
    directory_names = directories | {name for name, member in entries.items() if member.isdir()} | {"."}
    graph = {name: set() for name in directory_names}
    for name in directory_names - {"."}:
        graph[str(PurePosixPath(name).parent)].add(name)
    for name, _, is_directory in validated:
        if is_directory:
            graph[str(PurePosixPath(name).parent)].add(resolved[name])
    incoming = dict.fromkeys(graph, 0)
    for children in graph.values():
        for child in children:
            incoming[child] += 1
    ready = [name for name, count in incoming.items() if count == 0]
    processed = 0
    while ready:
        processed += 1
        for child in graph[ready.pop()]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if processed != len(graph):
        raise ValueError("cyclic archive directory link")
    return validated


def extract_tar(archive: tarfile.TarFile, dest: str | Path, *, strip_components=0, utf8_paths=False, allow_symlinks=False) -> None:
    if type(strip_components) is not int or strip_components < 0:
        raise ValueError("invalid archive prefix count")
    if type(utf8_paths) is not bool or (utf8_paths and os.name != "posix"):
        raise ValueError("UTF-8 byte paths require POSIX")
    if type(allow_symlinks) is not bool:
        raise ValueError("invalid archive link policy")
    root = (Path(os.path.realpath(str(dest).encode("utf-8")).decode("utf-8"))
            if utf8_paths else Path(dest).resolve())
    members = archive.getmembers()
    if len(members) > MAX_EXTRACT_FILES or sum(m.size for m in members) > MAX_EXTRACT_BYTES:
        raise ValueError("archive exceeds extraction limits")
    selected = []
    prefixes = set()
    for member in members:
        _target(root, member.name, utf8_paths=utf8_paths)
        if not (member.isfile() or member.isdir() or allow_symlinks and member.issym()):
            raise ValueError("archive links and special files are forbidden")
        parts = PurePosixPath(member.name.replace("\\", "/")).parts
        if len(parts) <= strip_components:
            if member.issym():
                raise ValueError("archive link cannot be stripped away")
            continue
        if strip_components:
            prefixes.add(parts[:strip_components])
            if len(prefixes) > 1:
                raise ValueError("archive has inconsistent root directories")
        name = "/".join(parts[strip_components:])
        _target(root, name, utf8_paths=utf8_paths)
        selected.append((member, name))
    links = _tar_links(selected, archive=archive)
    def filesystem_path(path):
        return str(path).encode("utf-8") if utf8_paths else path

    os.makedirs(filesystem_path(root), exist_ok=True)
    for member, name in selected:
        if member.issym():
            continue
        target = _target(root, name, utf8_paths=utf8_paths)
        if member.isdir():
            os.makedirs(filesystem_path(target), exist_ok=True)
        else:
            os.makedirs(filesystem_path(target.parent), exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("archive file missing")
            with source, open(filesystem_path(target), "wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(filesystem_path(target), 0o755 if member.mode & 0o111 else 0o644)
    # No payload writes occur through an archive-created link.
    for name, linked, is_directory in links:
        target = _target(root, name, utf8_paths=utf8_paths)
        _target(root, linked, utf8_paths=utf8_paths)
        os.makedirs(filesystem_path(target.parent), exist_ok=True)
        relative = posixpath.relpath(linked, posixpath.dirname(name) or ".")
        if os.name == "nt":
            relative = relative.replace("/", os.sep)
        os.symlink(relative.encode("utf-8") if utf8_paths else relative,
                   filesystem_path(target), target_is_directory=is_directory)


def extract_tar_files(archive: tarfile.TarFile, dest: str | Path, select) -> None:
    """Map allowlisted regular files; unselected links are metadata, never followed."""
    root = Path(dest).resolve()
    members = archive.getmembers()
    if len(members) > MAX_EXTRACT_FILES or any(member.size < 0 for member in members) or sum(member.size for member in members) > MAX_EXTRACT_BYTES:
        raise ValueError("archive exceeds extraction limits")
    selected, names = [], set()
    for member in members:
        _target(root, member.name)
        name = str(PurePosixPath(member.name.replace("\\", "/")))
        if name in names:
            raise ValueError("duplicate archive member")
        names.add(name)
        mapped = select(name, member)
        if mapped is None:
            continue
        if not member.isfile():
            raise ValueError("selected archive member must be a regular file")
        target = _target(root, mapped)
        if target == root or target.exists() or target.is_symlink():
            raise ValueError("selected archive output already exists or is invalid")
        selected.append((member, str(PurePosixPath(mapped.replace("\\", "/")))))
    _tar_links(selected, archive=archive)
    root.mkdir(parents=True, exist_ok=True)
    for member, name in selected:
        target = _target(root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("archive file missing")
        try:
            output = target.open("xb")
        except BaseException:
            source.close()
            raise
        try:
            with source, output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
        except BaseException:
            target.unlink(missing_ok=True)
            raise


def _zip_layout(entries, *, archive=None) -> None:
    """Reject normalized duplicates and file/parent conflicts before writing."""
    declared, spellings, directories = {}, {}, set()
    budget = _LayoutBudget(archive)
    for name, is_directory in entries:
        parts = PurePosixPath(name).parts
        if not parts:
            raise ValueError("invalid archive root member")
        for depth in range(1, len(parts) + 1):
            spelling = "/".join(parts[:depth])
            key = spelling.casefold() if os.name == "nt" else spelling
            if key in spellings and spellings[key] != spelling:
                raise ValueError("archive path aliases another member")
            budget.add(key, spelling)
            spellings[key] = spelling
            if depth < len(parts):
                directories.add(key)
        if key in declared:
            raise ValueError("duplicate archive member")
        declared[key] = is_directory
    if any(not declared.get(name, True) for name in directories):
        raise ValueError("archive writes beneath a file")


def _zip_output(root: Path, name: str, is_directory: bool) -> Path:
    target = _target(root, name)
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("archive output traverses a link")
        if current.exists() and (not current.is_dir() or current == target and not is_directory):
            raise ValueError("archive output already exists or conflicts")
    return target


def extract_zip(archive: zipfile.ZipFile, dest: str | Path, *, strip_components: int = 0, preserve_mode=False) -> None:
    if type(strip_components) is not int or strip_components < 0:
        raise ValueError("invalid archive prefix count")
    if type(preserve_mode) is not bool:
        raise ValueError("invalid archive permissions policy")
    destination = Path(dest)
    if destination.is_symlink() or getattr(destination, "is_junction", lambda: False)():
        raise ValueError("archive output traverses a link")
    root = destination.resolve()
    members = archive.infolist()
    if (len(members) > MAX_EXTRACT_FILES or any(m.file_size < 0 for m in members)
            or sum(m.file_size for m in members) > MAX_EXTRACT_BYTES):
        raise ValueError("archive exceeds extraction limits")
    selected = []
    originals = []
    prefixes = set()
    for member in members:
        _target(root, member.filename)
        mode = member.external_attr >> 16
        if mode & 0o170000 not in (0, 0o100000, 0o040000):
            raise ValueError("archive links and special files are forbidden")
        kind = mode & 0o170000
        if kind and (kind == 0o040000) != member.is_dir():
            raise ValueError("archive member type conflicts with its path")
        parts = PurePosixPath(member.filename.replace("\\", "/")).parts
        originals.append(("/".join(parts), member.is_dir()))
        if len(parts) <= strip_components:
            continue
        if strip_components:
            prefixes.add(parts[:strip_components])
            if len(prefixes) > 1:
                raise ValueError("archive has inconsistent root directories")
        name = "/".join(parts[strip_components:])
        _target(root, name)
        selected.append((member, name))
    _zip_layout(originals, archive=archive)
    _zip_layout(((name, member.is_dir()) for member, name in selected), archive=archive)
    for member, name in selected:
        _zip_output(root, name, member.is_dir())
    root.mkdir(parents=True, exist_ok=True)
    for member, name in selected:
        target = _zip_output(root, name, member.is_dir())
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source:
                output = target.open("xb")
                try:
                    with output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    if preserve_mode:
                        target.chmod(0o755 if member.external_attr >> 16 & 0o111 else 0o644)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
