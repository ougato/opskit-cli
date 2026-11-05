"""Fixed Git object reader. Only launch inside the platform native sandbox."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from core.plugin_git_archive import _OID, _records, archive_candidate

_PHASE = "request"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate fetch request field")
        result[key] = value
    return result


def _work_quota(work):
    count, size = 0, 0
    pending = [work]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if entry.is_symlink() or getattr(Path(entry.path), "is_junction", lambda: False)():
                    raise ValueError("linked native Git output")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                else:
                    size += entry.stat(follow_symlinks=False).st_size
                if count > 16384 or size > 256 * 1024**2:
                    raise ValueError("native Git working space limit exceeded")


def _dulwich():
    from dulwich.object_store import iter_tree_contents
    from dulwich.objects import Blob, Commit
    from dulwich.repo import Repo
    return Blob, Commit, Repo, iter_tree_contents


def _export(repository, commit_id, work, deadline):
    Blob, Commit, _, iter_tree_contents = _dulwich()
    commit = repository[commit_id]
    if not isinstance(commit, Commit):
        raise ValueError("selected object is not a commit")
    raw = bytearray()
    blobs = {}
    count = 0
    for item in iter_tree_contents(repository.object_store, commit.tree):
        if time.monotonic() >= deadline or count >= 8192:
            raise ValueError("native fetch tree budget exceeded")
        count += 1
        path, oid = item.path, bytes.fromhex(item.sha.decode("ascii"))
        mode = item.mode
        if mode not in (0o100644, 0o100755):
            raise ValueError("unsupported Git tree entry")
        blob = repository[oid]
        if not isinstance(blob, Blob) or len(blob.data) > 16 * 1024**2:
            raise ValueError("invalid Git blob")
        blobs[oid] = blob.data
        raw.extend((f"{mode:o} blob {oid.hex()} {len(blob.data)}\t".encode("ascii")
                    + path + b"\0"))
    entries = _records(bytes(raw))
    entries.sort(key=lambda entry: entry[0].encode("utf-8"))
    def read_blobs(requests, output):
        for _, _, oid, size in entries:
            if time.monotonic() >= deadline:
                raise ValueError("native fetch deadline exceeded")
            body = blobs[bytes.fromhex(oid.decode("ascii"))]
            blob = repository[bytes.fromhex(oid.decode("ascii"))]
            if len(body) != size or blob.id != oid:
                raise ValueError("Git blob identity mismatch")
            output.write(oid + b" blob " + str(size).encode() + b"\n")
            output.write(body + b"\n")
    commit_text = commit_id if _OID.fullmatch(commit_id) else commit_id.hex().encode("ascii")
    return archive_candidate(entries, commit_text, work, deadline, read_blobs)


def fetch_local(source, work, branch=None):
    """Read committed objects only with Dulwich; no Git executable or checkout."""
    if sys.platform != "win32":
        raise ValueError("native fetch platform pending")
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
                               or branch.startswith("-") or any(ord(c) < 32 for c in branch)):
        raise ValueError("invalid fetch branch")
    # Linked worktrees and .git redirect files are not copied or followed.
    repository = source / ".git" if (source / ".git").is_dir() else source
    if not (repository / "objects").is_dir() or not (repository / "HEAD").is_file():
        raise ValueError("ordinary Git repository required")
    deadline = time.monotonic() + 300
    Blob, Commit, Repo, _ = _dulwich()
    del Blob
    global _PHASE
    _PHASE = "open-local"
    repo = Repo(str(repository))
    try:
        _PHASE = "read-local-commit"
        if branch is None:
            commit_id = repo.head()
        else:
            from dulwich.refs import check_ref_format
            ref = b"refs/heads/" + branch.encode()
            if not check_ref_format(ref):
                raise ValueError("invalid fetch branch")
            commit_id = repo.refs[ref]
        if not isinstance(repo[commit_id], Commit):
            raise ValueError("selected object is not a commit")
        return _export(repo, commit_id, work, deadline)
    finally:
        repo.close()


def fetch_pack(source, work, pack):
    """Index only downloaded pack bytes inside the no-network AppContainer."""
    global _PHASE
    _PHASE = "validate-pack"
    if (sys.platform != "win32" or not isinstance(pack, dict) or set(pack) != {"commit", "shallow"}
            or not isinstance(pack["commit"], str) or not _OID.fullmatch(pack["commit"].encode())
            or not isinstance(pack["shallow"], list) or len(pack["shallow"]) > 64
            or any(not isinstance(oid, str) or not _OID.fullmatch(oid.encode())
                   or len(oid) != len(pack["commit"]) for oid in pack["shallow"])):
        raise ValueError("invalid native Git pack request")
    repository = work / "repository"
    _PHASE = "load-dulwich"
    Blob, Commit, Repo, _ = _dulwich()
    del Blob
    _PHASE = "open-object-store"
    repo = Repo(str(repository))
    packed = source / "objects.pack"
    if not 32 <= packed.stat().st_size <= 64 * 1024**2:
        raise ValueError("native Git pack size limit exceeded")
    deadline = time.monotonic() + 300
    try:
        _PHASE = "read-pack"
        with packed.open("rb") as stream:
            body = stream.read(64 * 1024**2 + 1)
        if len(body) != packed.stat().st_size:
            raise ValueError("native Git pack changed")
        _PHASE = "write-pack"
        original_chmod = os.chmod
        # AppContainer ACLs enforce access; Windows does not permit Git's POSIX mode changes here.
        os.chmod = lambda *args, **kwargs: None
        try:
            pack_stream, finish, abort = repo.object_store.add_pack()
            try:
                pack_stream.write(body)
                _PHASE = "index-pack"
                indexed = finish()
            except BaseException:
                abort()
                raise
        finally:
            os.chmod = original_chmod
        if indexed is None:
            raise ValueError("empty Git pack")
        _work_quota(work)
        _PHASE = "read-selected-commit"
        commit_id = bytes.fromhex(pack["commit"])
        if not isinstance(repo[commit_id], Commit):
            raise ValueError("selected object is not a commit")
        _PHASE = "export-tree"
        result = _export(repo, commit_id, work, deadline)
        _work_quota(work)
        return result
    finally:
        repo.close()


def main(request_file):
    global _PHASE
    # Host creates this bounded immutable file; never accept arbitrary argv or
    # paths from plugin code. Host independently validates the resulting archive.
    _PHASE = "read-request"
    with Path(request_file).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("native fetch request limit exceeded")
    _PHASE = "decode-request"
    request = json.loads(raw, object_pairs_hook=_object)
    _PHASE = "validate-request"
    if (not isinstance(request, dict) or set(request) != {"source", "work", "branch", "pack"}):
        raise ValueError("invalid native fetch request")
    if request["pack"] is None:
        metadata, archive = fetch_local(Path(request["source"]), Path(request["work"]), request["branch"])
    else:
        if request["branch"] is not None:
            raise ValueError("ambiguous native Git selection")
        metadata, archive = fetch_pack(Path(request["source"]), Path(request["work"]), request["pack"])
    _PHASE = "write-response"
    sys.stdout.buffer.write(json.dumps(metadata, separators=(",", ":")).encode() + b"\n")
    _PHASE = "copy-archive"
    with archive.open("rb") as stream:
        shutil.copyfileobj(stream, sys.stdout.buffer, 65536)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as error:
        # Fixed exception category only; never expose repository paths or tool output.
        code = (getattr(error, "winerror", None) or getattr(error, "errno", None)
                if isinstance(error, OSError) else None)
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", kind):
            kind = "WorkerError"
        sys.stdout.buffer.write(b"OPSKIT_FETCH_FAILURE:" + json.dumps({
            "kind": kind, "code": code, "phase": _PHASE}).encode("ascii") + b"\n")
        sys.stdout.buffer.flush()
        sys.exit(1)
