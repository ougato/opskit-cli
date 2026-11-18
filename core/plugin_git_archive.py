"""Pure bounded Git tree and archive validation shared by native backends."""
import hashlib
import re
import tarfile
import tempfile
import time
import unicodedata

MAX_ENTRIES = 8192


MAX_TREE_BYTES = 64 * 1024**2


MAX_FILE_BYTES = 16 * 1024**2


MAX_INDEX_BYTES = 2 * 1024**2


MAX_ARCHIVE_BYTES = 80 * 1024**2


_OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")


_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in "123456789¹²³"), *(f"lpt{i}" for i in "123456789¹²³")}


def _path(raw):
    if not raw or len(raw) > 1024:
        raise ValueError("invalid candidate path")
    name = raw.decode("utf-8", "strict")
    for part in name.split("/"):
        if (not part or part in (".", "..") or len(part.encode()) > 255
                or part.casefold() == ".git" or re.fullmatch(r"\.?git~[0-9]+", part.casefold())
                or part.endswith((".", " "))
                or part.split(".", 1)[0].casefold() in _DEVICES
                or any(ord(c) < 32 or ord(c) == 127 or unicodedata.category(c) == "Cf"
                       or c in '\\:<>"|?*' for c in part)):
            raise ValueError("invalid candidate path")
    return name


def _records(raw):
    if len(raw) > MAX_INDEX_BYTES or (raw and not raw.endswith(b"\0")):
        raise ValueError("invalid candidate index")
    result, files, directories, spellings, total = [], set(), set(), {}, 0
    for record in raw.split(b"\0")[:-1]:
        header, name = record.split(b"\t", 1)
        mode, kind, oid, size = header.split()
        if mode not in (b"100644", b"100755") or kind != b"blob" or not _OID.fullmatch(oid):
            raise ValueError("candidate contains unsupported entries")
        if not size.isdigit() or len(size) > 12:
            raise ValueError("invalid candidate size")
        count = int(size)
        total += count
        path = _path(name)
        # Reject case collisions, including directory/file prefixes, on all hosts.
        components = path.split("/")
        for index in range(1, len(components) + 1):
            spelling = "/".join(components[:index])
            key = unicodedata.normalize("NFD", spelling).casefold()
            if key in spellings and spellings[key] != spelling:
                raise ValueError("candidate path collision")
            if key in files or (index == len(components) and key in directories):
                raise ValueError("candidate path collision")
            spellings[key] = spelling
            (files if index == len(components) else directories).add(key)
            if len(files) + len(directories) > MAX_ENTRIES:
                raise ValueError("candidate budget exceeded")
        result.append((path, int(mode, 8) & 0o777, oid, count))
        if len(result) > MAX_ENTRIES or count > MAX_FILE_BYTES or total > MAX_TREE_BYTES:
            raise ValueError("candidate budget exceeded")
    return result


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("fetch deadline exceeded")
    return remaining


def archive_candidate(entries, commit, work, deadline, read_blobs):
    """Build the same bounded, validated result for any isolated Git backend."""
    candidate = work / "candidate.tar"
    with tempfile.TemporaryFile(dir=work) as requests, tempfile.TemporaryFile(dir=work) as blobs:
        for _, _, oid, _ in entries:
            requests.write(oid + b"\n")
        requests.seek(0)
        read_blobs(requests, blobs)
        blobs.seek(0)
        with tarfile.open(candidate, "x", format=tarfile.PAX_FORMAT) as archive:
            for path, mode, oid, size in entries:
                _remaining(deadline)
                if blobs.readline(256) != oid + b" blob " + str(size).encode() + b"\n":
                    raise ValueError("invalid fetched blob")
                item = tarfile.TarInfo(path)
                item.mode, item.size, item.mtime = mode, size, 0
                archive.addfile(item, blobs)
                if blobs.read(1) != b"\n":
                    raise ValueError("invalid fetched blob")
            if blobs.read(1):
                raise ValueError("unexpected fetched content")
    if candidate.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("candidate archive budget exceeded")
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for body in iter(lambda: stream.read(65536), b""):
            _remaining(deadline)
            digest.update(body)
    return {"schema_version": 1, "commit": commit.decode(), "entries": len(entries),
            "bytes": sum(item[3] for item in entries), "archive_bytes": candidate.stat().st_size,
            "archive_sha256": digest.hexdigest()}, candidate


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate fetch request field")
        result[key] = value
    return result


