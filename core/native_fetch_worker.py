"""Fixed Git object reader. Only launch inside the platform native sandbox."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

from core.plugin_git_archive import (
    MAX_INDEX_BYTES, MAX_TREE_BYTES, _OID, _records, _object, archive_candidate,
)


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


def _git_path(path):
    """Git normalizes slash paths itself; Win32 extended argv is not portable."""
    value = str(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return value.replace("\\", "/")


def _run(git, arguments, work, deadline, limit, *, input_file=None, output_file=None):
    """Bound stdout while Git is running, including cat-file's file output."""
    environment = {"SystemRoot": os.environ["SystemRoot"], "PATH": str(git.parent),
                   "HOME": _git_path(work / "home"), "USERPROFILE": _git_path(work / "home"),
                   "TEMP": str(work), "TMP": str(work), "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": _git_path(work / "empty-config"),
                   "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
                   "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "", "LC_ALL": "C"}
    args = [str(git), "--no-pager", "-c", "core.longpaths=true",
            "-c", "core.fsmonitor=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0",
            "-c", "core.hooksPath=" + _git_path(work / "empty"),
            "-c", "core.attributesFile=" + _git_path(work / "empty-config"),
            "-c", "protocol.allow=never", *arguments]
    process = subprocess.Popen(args, cwd=work, env=environment,
                               stdin=input_file if input_file is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pending = queue.Queue(maxsize=4)
    stopped = threading.Event()

    def deliver(value):
        while not stopped.is_set():
            try:
                pending.put(value, timeout=.05)
                return
            except queue.Full:
                pass

    def read():
        try:
            while not stopped.is_set():
                body = process.stdout.read1(65536)
                if not body:
                    break
                deliver(body)
        except (OSError, ValueError):
            deliver(False)
        finally:
            deliver(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    result = bytearray()
    total = 0
    next_quota = 0
    try:
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("native fetch deadline exceeded")
            if time.monotonic() >= next_quota:
                _work_quota(work)
                next_quota = time.monotonic() + .1
            try:
                body = pending.get(timeout=.05)
            except queue.Empty:
                continue
            if body is None:
                break
            if body is False:
                raise ValueError("native Git output failed")
            total += len(body)
            if total > limit:
                raise ValueError("native Git output limit exceeded")
            if output_file is None:
                result.extend(body)
            else:
                output_file.write(body)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError("native Git command failed")
        return bytes(result)
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        reader.join(timeout=3)
        if reader.is_alive():
            raise ValueError("native Git pipe cleanup failed")
        process.stdout.close()


def fetch_local(git, source, work, branch=None):
    """Read committed objects only: no checkout, hooks, clone or host Git fallback."""
    if sys.platform != "win32":
        raise ValueError("native fetch platform pending")
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
                               or branch.startswith("-") or any(ord(c) < 32 for c in branch)):
        raise ValueError("invalid fetch branch")
    (work / "home").mkdir()
    (work / "empty").mkdir()
    (work / "empty-config").write_bytes(b"")
    # Linked worktrees and .git redirect files are not copied or followed.
    repository = source / ".git" if (source / ".git").is_dir() else source
    if not (repository / "objects").is_dir() or not (repository / "HEAD").is_file():
        raise ValueError("ordinary Git repository required")
    prefix = ["--git-dir=" + _git_path(repository)]
    deadline = time.monotonic() + 300
    if branch is not None:
        _run(git, [*prefix, "check-ref-format", "--branch", branch], work, deadline, 1024)
    revision = "HEAD" if branch is None else "refs/heads/" + branch
    commit = _run(git, [*prefix, "rev-parse", "--verify", revision + "^{commit}"],
                  work, deadline, 128).strip()
    return _export(git, repository, commit, work, deadline)


def _export(git, repository, commit, work, deadline):
    if not _OID.fullmatch(commit):
        raise ValueError("invalid fetched commit")
    prefix = ["--git-dir=" + _git_path(repository)]
    entries = _records(_run(git, [*prefix, "ls-tree", "-r", "-z", "-l", "--full-tree", commit.decode()],
                            work, deadline, MAX_INDEX_BYTES))
    return archive_candidate(entries, commit, work, deadline,
                             lambda requests, blobs: _run(git, [*prefix, "cat-file", "--batch"],
                                                          work, deadline, MAX_TREE_BYTES + MAX_INDEX_BYTES,
                                                          input_file=requests, output_file=blobs))


def fetch_pack(git, source, work, pack):
    """Index only downloaded pack bytes inside the no-network AppContainer."""
    if (sys.platform != "win32" or not isinstance(pack, dict) or set(pack) != {"commit", "shallow"}
            or not isinstance(pack["commit"], str) or not _OID.fullmatch(pack["commit"].encode())
            or not isinstance(pack["shallow"], list) or len(pack["shallow"]) > 64
            or any(not isinstance(oid, str) or not _OID.fullmatch(oid.encode())
                   or len(oid) != len(pack["commit"]) for oid in pack["shallow"])):
        raise ValueError("invalid native Git pack request")
    (work / "home").mkdir()
    (work / "empty").mkdir()
    (work / "empty-config").write_bytes(b"")
    repository = work / "repository"
    (repository / "objects" / "pack").mkdir(parents=True)
    (repository / "refs" / "heads").mkdir(parents=True)
    (repository / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
    sha256 = len(pack["commit"]) == 64
    (repository / "config").write_text("[core]\n bare = true\n repositoryformatversion = " +
                                       ("1\n[extensions]\n objectFormat = sha256\n" if sha256 else "0\n"), encoding="ascii")
    if pack["shallow"]:
        (repository / "shallow").write_text("\n".join(pack["shallow"]) + "\n", encoding="ascii")
    packed = source / "objects.pack"
    if not 32 <= packed.stat().st_size <= 64 * 1024**2:
        raise ValueError("native Git pack size limit exceeded")
    deadline = time.monotonic() + 300
    prefix = ["--git-dir=" + _git_path(repository)]
    with packed.open("rb") as stream:
        _run(git, [*prefix, "index-pack", "--stdin", "--strict", "--max-input-size=67108864"],
             work, deadline, 1024, input_file=stream)
    commit = _run(git, [*prefix, "rev-parse", "--verify", pack["commit"] + "^{commit}"],
                  work, deadline, 128).strip()
    if commit != pack["commit"].encode():
        raise ValueError("native Git selected commit mismatch")
    return _export(git, repository, commit, work, deadline)


def main(request_file):
    # Host creates this bounded immutable file; never accept arbitrary argv or
    # paths from plugin code. Host independently validates the resulting archive.
    with Path(request_file).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("native fetch request limit exceeded")
    request = json.loads(raw, object_pairs_hook=_object)
    if (not isinstance(request, dict) or set(request) != {"git", "source", "work", "branch", "pack"}):
        raise ValueError("invalid native fetch request")
    if request["pack"] is None:
        metadata, archive = fetch_local(Path(request["git"]), Path(request["source"]),
                                        Path(request["work"]), request["branch"])
    else:
        if request["branch"] is not None:
            raise ValueError("ambiguous native Git selection")
        metadata, archive = fetch_pack(Path(request["git"]), Path(request["source"]),
                                      Path(request["work"]), request["pack"])
    sys.stdout.buffer.write(json.dumps(metadata, separators=(",", ":")).encode() + b"\n")
    with archive.open("rb") as stream:
        shutil.copyfileobj(stream, sys.stdout.buffer, 65536)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # No repository path, credentials or tool output in diagnostics.
        sys.exit(1)
