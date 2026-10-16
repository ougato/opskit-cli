"""Standalone Linux fetch guest. Never import host platform or plugin modules."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata

MAX_ENTRIES = 8192
MAX_TREE_BYTES = 64 * 1024**2
MAX_FILE_BYTES = 16 * 1024**2
MAX_INDEX_BYTES = 2 * 1024**2
MAX_ARCHIVE_BYTES = 80 * 1024**2
DEADLINE_SECONDS = 300
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


def _environment(work):
    return {"PATH": "/usr/bin:/bin", "HOME": str(work / "home"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(work / "home" / "gitconfig"), "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false"}


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("fetch deadline exceeded")
    return remaining


def _file_limit(limit):
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


def _run(args, work, deadline, limit, *, input_file=None, output_file=None):
    _remaining(deadline)
    with tempfile.TemporaryFile(dir=work) as captured:
        process = subprocess.Popen(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null",
             *args],
            cwd=work, env=_environment(work), stdin=input_file if input_file is not None else subprocess.DEVNULL,
            stdout=output_file if output_file is not None else captured, stderr=subprocess.DEVNULL,
            start_new_session=True, preexec_fn=lambda: _file_limit(limit))
        try:
            try:
                code = process.wait(timeout=_remaining(deadline))
            except subprocess.TimeoutExpired:
                raise ValueError("fetch deadline exceeded") from None
            if code:
                raise ValueError("isolated Git command failed")
            if output_file is None:
                captured.seek(0)
                body = captured.read(limit + 1)
                if len(body) > limit:
                    raise ValueError("fetch output budget exceeded")
                return body
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def fetch_local(source, work, branch=None):
    """Read one read-only mounted source into a fresh private guest workspace.

    Caller must enforce container mount/network/tmpfs/resource isolation. This
    guest function is never a host-Git fallback and does not publish a plugin.
    """
    return _fetch(source, work, branch)


def fetch_https(source, profile, proxy, work, branch=None):
    """Fetch through an explicit proxy; credentials never enter the Git argv."""
    from core.plugin_fetch_https import configure
    return _fetch(source, work, branch, configure=lambda root: configure(source, profile, proxy, root))


def fetch_ssh(source, profile, proxy, work, branch=None):
    from core.plugin_fetch_ssh import configure
    return _fetch(source, work, branch, configure=lambda root: configure(source, profile, proxy, root))


def _fetch(source, work, branch, configure=None):
    if sys.platform != "linux":
        raise ValueError("fetch guest requires Linux isolation")
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
                               or branch.startswith("-") or any(ord(c) < 32 for c in branch)):
        raise ValueError("invalid fetch branch")
    work = Path(work)
    (work / "home").mkdir(mode=0o700)
    # upload-pack does not inherit clone's command-line configuration. Supply
    # only fixed read-only mount identities through a private guest-owned file.
    if configure is None:
        config = work / "home" / "gitconfig"
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write('[protocol]\n allow = never\n[protocol "file"]\n allow = always\n'
                         '[safe]\n directory = /source\n directory = /source/.git\n')
    else:
        source = configure(work)
    deadline = time.monotonic() + DEADLINE_SECONDS
    if branch is not None:
        _run(["check-ref-format", "--branch", branch], work, deadline, 1024)
    args = ["clone", "--bare", "--no-local", "--depth=1", "--single-branch", "--template=/empty"]
    if branch is not None:
        args += ["--branch", branch]
    args += ["--", str(source), str(work / "repository")]
    _run(args, work, deadline, MAX_TREE_BYTES)
    git = ["--git-dir=" + str(work / "repository")]
    commit = _run([*git, "rev-parse", "--verify", "HEAD^{commit}"], work, deadline, 128).strip()
    if not _OID.fullmatch(commit):
        raise ValueError("invalid fetched commit")
    entries = _records(_run([*git, "ls-tree", "-r", "-z", "-l", "--full-tree", commit.decode()],
                            work, deadline, MAX_INDEX_BYTES))
    candidate = work / "candidate.tar"
    with tempfile.TemporaryFile(dir=work) as requests, tempfile.TemporaryFile(dir=work) as blobs:
        for _, _, oid, _ in entries:
            requests.write(oid + b"\n")
        requests.seek(0)
        _run([*git, "cat-file", "--batch"], work, deadline, MAX_TREE_BYTES + MAX_INDEX_BYTES,
             input_file=requests, output_file=blobs)
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


def main():
    def expired(signum, frame):
        raise ValueError("fetch deadline exceeded")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    try:
        status = Path("/proc/self/status").read_text()
        if (os.geteuid() != 65534 or os.getegid() != 65534
                or "CapEff:\t0000000000000000" not in status or "NoNewPrivs:\t1" not in status
                or "Seccomp:\t2" not in status
                or not os.statvfs("/").f_flag & os.ST_RDONLY):
            raise ValueError("invalid fetch guest boundary")
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            raise ValueError("fetch request budget exceeded")
        request = json.loads(raw, object_pairs_hook=_object)
        if (not isinstance(request, dict) or type(request.get("schema_version")) is not int
                or request["schema_version"] != 1):
            raise ValueError("invalid fetch request")
        kind = request.get("kind")
        expected = {"schema_version", "kind", "branch"}
        if kind in ("https", "ssh"):
            expected |= {"source", "profile", "proxy"}
        if kind not in ("local", "https", "ssh") or set(request) != expected:
            raise ValueError("invalid fetch request")
        if kind == "local" and (len(raw) > 4096 or not os.statvfs("/source").f_flag & os.ST_RDONLY):
            raise ValueError("invalid local fetch boundary")
        with tempfile.TemporaryDirectory(prefix="fetch-", dir="/work") as work:
            if kind == "local":
                metadata, candidate = fetch_local("/source", work, request["branch"])
            else:
                fetch = fetch_https if kind == "https" else fetch_ssh
                metadata, candidate = fetch(request["source"], request["profile"], request["proxy"], work, request["branch"])
            sys.stdout.buffer.write(json.dumps(metadata, separators=(",", ":")).encode() + b"\n")
            with candidate.open("rb") as stream:
                shutil.copyfileobj(stream, sys.stdout.buffer, 65536)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("isolated fetch failed", file=sys.stderr)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    # -I excludes the script directory. Only the immutable image runtime is
    # added; no host source or guest-writable directory is an import location.
    sys.path.insert(0, str(Path(__file__).parent))
    sys.exit(main())
