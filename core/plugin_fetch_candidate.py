"""Host-side read-only validation of untrusted fetch guest output."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import re
import tarfile
import unicodedata
import time
import uuid

from core.archive import _unsafe_windows_component
from core.archive_reader import open_tar
from core.plugin_json import loads
from core.private_file import temporary_private_file

MAX_ENTRIES = 8192
MAX_FILE_BYTES = 16 * 1024**2
MAX_TREE_BYTES = 64 * 1024**2
MAX_ARCHIVE_BYTES = 80 * 1024**2
MAX_HEADER_BYTES = 1024


@dataclass(frozen=True)
class CandidateFile:
    path: str
    mode: int
    size: int
    sha256: str


@dataclass(frozen=True)
class CandidateInventory:
    # A guest claim, not a remote signature or authorization decision.
    reported_commit: str
    archive_sha256: str
    files: tuple[CandidateFile, ...]
    bytes: int


def _header(source):
    raw = source.readline(MAX_HEADER_BYTES + 1)
    if len(raw) > MAX_HEADER_BYTES or not raw.endswith(b"\n"):
        raise ValueError("invalid fetch response header")
    value = loads(raw.decode("utf-8"))
    keys = {"schema_version", "commit", "entries", "bytes", "archive_bytes", "archive_sha256"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid fetch response fields")
    for field, limit in (("schema_version", 1), ("entries", MAX_ENTRIES), ("bytes", MAX_TREE_BYTES),
                         ("archive_bytes", MAX_ARCHIVE_BYTES)):
        if type(value[field]) is not int or not 0 <= value[field] <= limit:
            raise ValueError("invalid fetch response budget")
    if (value["schema_version"] != 1 or value["archive_bytes"] < tarfile.RECORDSIZE
            or value["archive_bytes"] % tarfile.RECORDSIZE
            or not isinstance(value["commit"], str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value["commit"])
            or not isinstance(value["archive_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["archive_sha256"])):
        raise ValueError("invalid fetch response identity")
    return value


def _layout(name, nodes):
    if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 1024:
        raise ValueError("invalid candidate name")
    parts = name.split("/")
    for index, part in enumerate(parts, 1):
        if (not part or part in (".", "..") or len(part.encode("utf-8")) > 255
                or _unsafe_windows_component(part) or part.casefold() == ".git"
                or re.fullmatch(r"\.?git~[0-9]+", part.casefold())
                or any(c in "\\:" or ord(c) == 127 or unicodedata.category(c) == "Cf" for c in part)):
            raise ValueError("unsafe candidate name")
        spelling = "/".join(parts[:index])
        key = unicodedata.normalize("NFD", spelling).casefold()
        directory = index < len(parts)
        previous = nodes.get(key)
        if previous is not None and (previous != (spelling, True) or not directory):
            raise ValueError("candidate layout collision")
        if previous is None:
            if len(nodes) >= MAX_ENTRIES:
                raise ValueError("candidate node budget exceeded")
            nodes[key] = (spelling, directory)


@contextmanager
def validated_archive(source):
    """Keep validated archive bytes alive for a trusted candidate writer.

    Snapshot once before parsing; subsequent reads never consult the original
    response again. The yielded file is platform-owned and must not be modified
    or passed to a plugin. This is not publication, authorization or an I/O
    deadline boundary. The caller must supervise the complete operation.
    """
    header = _header(source)
    with temporary_private_file() as snapshot:
        digest = hashlib.sha256()
        remaining = header["archive_bytes"]
        while remaining:
            chunk = source.read(min(65536, remaining))
            if not chunk or len(chunk) > remaining:
                raise ValueError("truncated fetch archive")
            digest.update(chunk)
            snapshot.write(chunk)
            remaining -= len(chunk)
        if source.read(1) or digest.hexdigest() != header["archive_sha256"]:
            raise ValueError("fetch archive integrity mismatch")
        snapshot.seek(0)
        inventory = _inventory(snapshot, header)
        snapshot.seek(0)
        yield inventory, snapshot


def validate_output(source):
    """Return an inventory only; it does not authorize another response's bytes."""
    with validated_archive(source) as (inventory, _):
        return inventory


def _validation_worker(connection, response, request):
    from network.worker import send
    try:
        with response:
            response.seek(0)
            inventory = validate_output(response)
            response.seek(0)
            # 16 paths of at most 1024 UTF-8 bytes stay below the existing
            # 64KiB JSON frame bound even with ensure_ascii escaping.
            for offset in range(0, len(inventory.files), 16):
                send(connection, {"request": request, "page": offset // 16,
                                  "files": [[f.path, f.mode, f.size, f.sha256] for f in inventory.files[offset:offset + 16]]})
            send(connection, {"request": request, "done": True, "commit": inventory.reported_commit,
                              "archive": inventory.archive_sha256, "count": len(inventory.files), "bytes": inventory.bytes})
    except (OSError, ValueError, EOFError, tarfile.TarError):
        send(connection, {"request": request, "rejected": True})
    finally:
        connection.close()


def supervised_inventory(response, *, timeout):
    """Validate file I/O in an owned child; return bounded metadata pages only.

    The response must be an exclusively held platform file, flushed before
    transfer. It stays open and rewound on success. This supervises validation,
    not the caller's earlier capture writes or later publication operations.
    """
    from core.process_file import _SpawnFile
    from network.worker import owned_process, wait_result
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid candidate validation budget")
    deadline = time.monotonic() + timeout
    request = uuid.uuid4().hex
    files, nodes, total, pages = [], {}, 0, 0
    with owned_process(_validation_worker, (_SpawnFile(response.fileno()), request), label="candidate validation") as (receive, _):
        while True:
            result = wait_result(receive, deadline=deadline, label="candidate validation")
            if result == {"request": request, "rejected": True}:
                raise ValueError("candidate validation rejected")
            if result.get("request") != request:
                raise RuntimeError("invalid candidate validation binding")
            if set(result) == {"request", "page", "files"}:
                entries = result["files"]
                if (type(result["page"]) is not int or result["page"] != pages
                        or not isinstance(entries, list) or not 1 <= len(entries) <= 16
                        or len(files) + len(entries) > MAX_ENTRIES):
                    raise RuntimeError("invalid candidate validation page")
                for entry in entries:
                    if (not isinstance(entry, list) or len(entry) != 4
                            or type(entry[1]) is not int or entry[1] not in (0o644, 0o755)
                            or type(entry[2]) is not int or not 0 <= entry[2] <= MAX_FILE_BYTES
                            or not isinstance(entry[3], str) or not re.fullmatch(r"[0-9a-f]{64}", entry[3])):
                        raise RuntimeError("invalid candidate validation entry")
                    _layout(entry[0], nodes)
                    total += entry[2]
                    if total > MAX_TREE_BYTES:
                        raise RuntimeError("candidate validation byte budget exceeded")
                    files.append(CandidateFile(*entry))
                pages += 1
                continue
            if (set(result) != {"request", "done", "commit", "archive", "count", "bytes"}
                    or result["done"] is not True or type(result["count"]) is not int or result["count"] != len(files)
                    or type(result["bytes"]) is not int or result["bytes"] != total
                    or not isinstance(result["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result["commit"])
                    or not isinstance(result["archive"], str) or not re.fullmatch(r"[0-9a-f]{64}", result["archive"])):
                raise RuntimeError("invalid candidate validation result")
            if time.monotonic() >= deadline:
                raise TimeoutError("candidate validation deadline exceeded")
            return CandidateInventory(result["commit"], result["archive"], tuple(files), total)


def _inventory(snapshot, header):
    files, nodes, total, end = [], {}, 0, 0
    with open_tar(fileobj=snapshot, mode="r:", max_files=MAX_ENTRIES,
                  max_payload_bytes=MAX_TREE_BYTES, max_metadata_bytes=2 * 1024**2) as archive:
        for member in archive:
            if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.sparse is not None
                    or member.mode not in (0o644, 0o755) or member.uid != 0 or member.gid != 0
                    or member.uname or member.gname or member.linkname or member.mtime != 0
                    or member.devmajor or member.devminor or archive.pax_headers
                    or set(member.pax_headers) - {"path"} or not 0 <= member.size <= MAX_FILE_BYTES):
                raise ValueError("invalid candidate member")
            _layout(member.name, nodes)
            total += member.size
            if total > MAX_TREE_BYTES:
                raise ValueError("candidate byte budget exceeded")
            content = hashlib.sha256()
            with archive.extractfile(member) as stream:
                remaining = member.size
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("truncated candidate file")
                    remaining -= len(chunk)
                    content.update(chunk)
            files.append(CandidateFile(member.name, member.mode, member.size, content.hexdigest()))
            end = ((member.offset_data + member.size + 511) // 512) * 512
    if len(files) != header["entries"] or total != header["bytes"]:
        raise ValueError("fetch inventory mismatch")
    if header["archive_bytes"] - end < 1024:
        raise ValueError("missing candidate archive terminator")
    snapshot.seek(end)
    for chunk in iter(lambda: snapshot.read(65536), b""):
        if any(chunk):
            raise ValueError("unexpected content after candidate archive")
    return CandidateInventory(header["commit"], header["archive_sha256"], tuple(files), total)
