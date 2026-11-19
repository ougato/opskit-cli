"""Start a pinned platform worker and gate plugin execution on activation.

Callers own the workspace and must revalidate plugin identity, state and live
authorization before activating. The interpreter and its standard library must
match platform-pinned archives and the host SDK. Plugin code and plugin-specific
dependencies are never imported before activation.
"""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import io
from pathlib import Path, PurePosixPath
import queue
import secrets
import stat
import threading
import time
import zipfile

from core.native_process import open_worker
from core.native_protocol import exchange
from core.native_runtime import absolute_path
from core.native_sandbox import NativeSandboxError

_warming_lock = threading.Lock()
_warming_thread = None


@lru_cache(maxsize=4)
def _platform_interpreter_inventory(archive_path, size, sha256):
    """Derive executable bytes from the pinned archive without executing it."""
    from core.plugin_runtime_inventory import _read_file
    raw = _read_file(Path(archive_path), Path(Path(archive_path).anchor), size)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("platform interpreter archive identity mismatch")
    from core.native_runtime import MAX_BYTES, MAX_ENTRIES
    result = {}
    folded = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > MAX_ENTRIES:
            raise ValueError("platform interpreter archive too large")
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (path.is_absolute() or not path.parts or ".." in path.parts or "\\" in name
                    or any(any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                           or part.endswith((".", " ")) for part in path.parts)
                    or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1):
                raise ValueError("unsafe platform interpreter archive")
            if member.is_dir():
                continue
            relative = path.as_posix()
            if relative.casefold() in folded:
                raise ValueError("duplicate platform interpreter archive entry")
            total += member.file_size
            if total > MAX_BYTES:
                raise ValueError("platform interpreter archive too large")
            folded.add(relative.casefold())
            result[relative] = hashlib.sha256(archive.read(member)).hexdigest()
    if "python.exe" not in result:
        raise ValueError("platform interpreter missing executable")
    return result


@lru_cache(maxsize=32)
def _platform_wheel_inventory(archive_path, size, sha256):
    """Map one pinned wheel to installed file hashes without importing it."""
    from core.native_wheels import MAX_EXPANDED, MAX_MEMBERS, RESERVED
    from core.plugin_runtime_inventory import _read_file
    path = Path(archive_path)
    raw = _read_file(path, Path(path.anchor), size)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("platform wheel archive identity mismatch")
    result = {}
    folded = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS:
            raise ValueError("platform wheel entry count exceeded")
        for member in members:
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or not path.parts or ".." in path.parts
                    or "\\" in member.orig_filename or "\0" in member.orig_filename
                    or any(any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                           or part.endswith((".", " ")) or part.split(".")[0].casefold() in RESERVED
                           for part in path.parts)
                    or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1):
                raise ValueError("unsafe platform wheel entry")
            if member.is_dir():
                continue
            parts = path.parts
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                    raise ValueError("unsupported platform wheel layout")
                parts = parts[2:]
            if parts[-1].lower().endswith(".pth"):
                raise ValueError("platform wheel startup hook rejected")
            relative = PurePosixPath(*parts).as_posix()
            if relative.casefold() in folded:
                raise ValueError("duplicate platform wheel entry")
            total += member.file_size
            if total > MAX_EXPANDED:
                raise ValueError("platform wheel size exceeded")
            folded.add(relative.casefold())
            result[relative] = hashlib.sha256(archive.read(member)).hexdigest()
    return result


def warm_platform_assets():
    """Hash pinned archives without starting an interpreter or plugin code."""
    from core.paths import data_dir
    from core.plugin_bootstrap import _manifest
    root = data_dir() / "cache" / "plugin-bootstrap"
    for asset in _manifest():
        if asset["kind"] == "python":
            _platform_interpreter_inventory(str(root / asset["filename"]), asset["size"], asset["sha256"])
        elif asset["kind"] == "wheel":
            _platform_wheel_inventory(str(root / asset["filename"]), asset["size"], asset["sha256"])


def warm_platform_assets_async():
    """Overlap bounded, read-only archive inspection with other setup."""
    global _warming_thread
    with _warming_lock:
        if _warming_thread is not None and _warming_thread.is_alive():
            return

        def warm():
            try:
                warm_platform_assets()
            except Exception:
                # Selection still independently verifies every required asset;
                # failed or missing archives never make a worker eligible.
                pass

        _warming_thread = threading.Thread(target=warm, daemon=True, name="plugin-platform-assets")
        _warming_thread.start()


def _verify_platform_runtime(base):
    """Admit only host SDK and exact pinned interpreter and wheel contents."""
    from core.native_runtime import verify
    from core.paths import data_dir
    from core.plugin_bootstrap import _manifest
    from core.plugin_runtime_inventory import snapshot
    assets = _manifest()
    python = [item for item in assets if item["kind"] == "python"]
    wheels = [item for item in assets if item["kind"] == "wheel"]
    if len(python) != 1 or not wheels:
        raise ValueError("platform runtime assets unavailable")
    root = data_dir() / "cache" / "plugin-bootstrap"
    asset = python[0]
    files = {"python/" + name: digest for name, digest in _platform_interpreter_inventory(
        str(root / asset["filename"]), asset["size"], asset["sha256"]).items()}
    folded = {name.casefold() for name in files}
    for asset in wheels:
        entries = _platform_wheel_inventory(str(root / asset["filename"]), asset["size"], asset["sha256"])
        for name, digest in entries.items():
            key = "site-packages/" + name
            if key.casefold() in folded or name.split("/", 1)[0].casefold() == "core":
                raise ValueError("conflicting platform wheel entry")
            folded.add(key.casefold())
            files[key] = digest
    for name, content in snapshot().items():
        key = "site-packages/" + name
        if key.casefold() in folded:
            raise ValueError("conflicting platform SDK entry")
        folded.add(key.casefold())
        files[key] = hashlib.sha256(content).hexdigest()
    verify(base / "runtime", files)


def _bootstrap(base, token):
    runtime = base / "runtime"
    request = base / "inputs" / "request.json"
    expected = ("OPSKIT_ACTIVATE:" + token + "\n").encode("ascii")
    ready = "OPSKIT_READY:" + token
    # The caller has verified the complete SDK and every dependency byte against
    # host-owned sources and pinned wheels; no plugin path is added here.
    return ("import sys,os; sys.path.insert(0," + repr(str(runtime / "site-packages")) + "); "
            "sys.argv=['core.plugin_worker'," + repr(str(request)) + "]; "
            "import core.plugin_worker as w,core.sdk,core.i18n as i,core.theme as t; "
            "i.init(language=os.environ.get('OPSKIT_LANGUAGE')); t.init_bundled_default(); "
            "print(" + repr(ready) + ",flush=True); "
            "sys.stdin.buffer.readline(128)==" + repr(expected) + " or sys.exit(3); w.main()")


def _wait(result, *, deadline, cancel_event=None, checkpoint=None):
    if type(deadline) not in (int, float) or not float("-inf") < deadline < float("inf"):
        raise ValueError("invalid native preload deadline")
    if time.monotonic() >= deadline:
        raise TimeoutError("native preload deadline exceeded")
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("native preload cancelled")
        if checkpoint is not None:
            checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native preload deadline exceeded")
        try:
            value = result.get(timeout=min(.05, remaining))
        except queue.Empty:
            continue
        if time.monotonic() >= deadline:
            raise TimeoutError("native preload deadline exceeded")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("native preload cancelled")
        return value


class PreloadedWorker:
    """A one-shot activation gate; closing the owner kills the child."""

    def __init__(self, child, token, startup_deadline):
        self.child = child
        self._token = token
        self._startup_deadline = startup_deadline
        self._readiness = queue.Queue(maxsize=1)
        self._readiness_done = threading.Event()
        self._ready_at = None
        self._lock = threading.Lock()
        self._state = "ready"
        self._closing = threading.Event()
        self._done = threading.Event()
        self._done.set()

    @property
    def ready_at(self):
        """When trusted SDK initialization completed, or None while pending."""
        return self._ready_at

    @property
    def startup_failed(self):
        return self._readiness_done.is_set() and self._ready_at is None

    def _publish_readiness(self, line):
        completed_at = time.monotonic()
        if (completed_at < self._startup_deadline
                and line.rstrip(b"\r\n") == ("OPSKIT_READY:" + self._token).encode("ascii")):
            self._ready_at = completed_at
        self._readiness.put((line, completed_at))
        self._readiness_done.set()

    def activate(self, *, deadline, dispatch, checkpoint, output, cancel_event=None, protocol_deadline=None):
        with self._lock:
            if self._state != "ready":
                raise RuntimeError("native preload is no longer available")
            self._state = "activated"
            self._done.clear()

        def check():
            if self._closing.is_set() or cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("native preload cancelled")
            checkpoint()

        signal = ("OPSKIT_ACTIVATE:" + self._token + "\n").encode("ascii")
        completed = queue.Queue(maxsize=1)

        def send():
            try:
                remaining = memoryview(signal)
                while remaining:
                    count = self.child.stdin.write(remaining)
                    if not count:
                        raise OSError("native preload input closed")
                    remaining = remaining[count:]
                self.child.stdin.flush()
                completed.put(True)
            except (OSError, ValueError):
                completed.put(False)

        sender = threading.Thread(target=send, daemon=True, name="native-preload-activate")
        try:
            check()
            # Host validation overlaps interpreter startup and trusted SDK
            # imports. Plugin activation still requires the readiness marker.
            from core.plugin_entry_preload import PreloadStale
            if time.monotonic() >= deadline:
                raise PreloadStale()
            try:
                line, completed_at = _wait(self._readiness, deadline=deadline,
                                           cancel_event=cancel_event, checkpoint=check)
            except TimeoutError as error:
                raise PreloadStale() from error
            if (line.rstrip(b"\r\n") != ("OPSKIT_READY:" + self._token).encode("ascii")
                    or completed_at >= self._startup_deadline):
                raise PreloadStale()
            check()
            sender.start()
            if not _wait(completed, deadline=deadline, cancel_event=cancel_event, checkpoint=check):
                raise NativeSandboxError("protocol_failed")
            check()
            return exchange(self.child, deadline=protocol_deadline if protocol_deadline is not None else deadline, dispatch=dispatch,
                            checkpoint=check, output=output)
        finally:
            # On a stuck writer the owning context kills the child before join.
            if sender.ident is not None:
                self._activation_thread = sender
            self._done.set()

    def _close(self):
        with self._lock:
            self._state = "closed"
            self._closing.set()
        if not self._done.wait(timeout=3):
            raise NativeSandboxError("transport_cleanup_failed")


@contextmanager
def open_preloaded_worker(backend, base, *, deadline, cancel_event=None):
    """Yield a prepared, inactive child owned by a private staged workspace.

    The deadline bounds bootstrap readiness after OS launch. The caller retains
    ownership of the workspace and must close this context on cancel.
    """
    if type(deadline) not in (int, float) or not time.monotonic() < deadline < float("inf"):
        raise ValueError("invalid native preload deadline")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("native preload cancelled")
    base = absolute_path(base)
    _verify_platform_runtime(base)
    if time.monotonic() >= deadline:
        raise TimeoutError("native preload deadline exceeded")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("native preload cancelled")
    token = secrets.token_hex(16)
    args = ["-I", "-X", "utf8", "-S", "-B", "-u", "-c", _bootstrap(base, token)]
    environment = {"OPSKIT_SANDBOX": "1", "OPSKIT_DATA_DIR": str(base / "data"),
                   "OPSKIT_PLUGIN_ROOT": str(base / "plugin"), "OPSKIT_SEED_ROOT": str(base / "seed"),
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    reader = None
    preloaded = None
    try:
        with open_worker(backend, str(base / "runtime" / "python" / "python.exe"), args,
                         readonly_trees=(base,), writable_trees=(base / "data",),
                         environment=environment, cwd=str(base / "data"),
                         filter_python_startup=True) as child:
            if time.monotonic() >= deadline:
                raise TimeoutError("native preload deadline exceeded")
            preloaded = PreloadedWorker(child, token, deadline)
            def read_ready():
                try:
                    line = child.stdout.readline(128)
                except (OSError, ValueError):
                    line = b""
                preloaded._publish_readiness(line)
            reader = threading.Thread(target=read_ready, daemon=True, name="native-preload-ready")
            reader.start()
            try:
                yield preloaded
            finally:
                preloaded._close()
    finally:
        # open_worker has killed the child and closed its endpoints at this point.
        for thread in (reader, getattr(preloaded, "_activation_thread", None)):
            if thread is not None:
                thread.join(timeout=3)
                if thread.is_alive():
                    raise NativeSandboxError("transport_cleanup_failed")
