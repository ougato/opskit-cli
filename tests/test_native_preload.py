"""Preload readiness and activation must fail closed and retire I/O."""
from contextlib import contextmanager
import hashlib
import queue
import re
import threading
import time
import zipfile

import pytest

from core import native_preload


class _Output:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self, limit):
        return self.lines.get()


class _Input:
    def __init__(self, *, blocked=False):
        self.received = bytearray()
        self.released = threading.Event()
        self.blocked = blocked

    def write(self, value):
        if self.blocked:
            self.released.wait()
            raise OSError("closed")
        self.received.extend(value)
        return len(value)

    def flush(self):
        pass


class _Child:
    def __init__(self, *, blocked=False):
        self.stdout = _Output()
        self.stdin = _Input(blocked=blocked)
        self.closed = False

    def close(self):
        self.closed = True
        self.stdout.lines.put(b"")
        self.stdin.released.set()


def _worker(monkeypatch, *, response="ready", blocked=False, sdk="ready"):
    child = _Child(blocked=blocked)
    monkeypatch.setattr(native_preload, "_verify_platform_runtime", lambda base: None)

    @contextmanager
    def open_worker(backend, executable, args, **options):
        match = re.search(r"OPSKIT_READY:([0-9a-f]{32})", args[-1])
        assert match is not None
        child.token = match.group(1)
        if response == "ready":
            if sdk == "ready":
                child.stdout.lines.put(("OPSKIT_READY:" + child.token + "\r\n").encode())
            elif sdk == "invalid":
                child.stdout.lines.put(b"sdk failed\n")
            elif sdk == "eof":
                child.stdout.lines.put(b"")
        elif response == "invalid":
            child.stdout.lines.put(b"incorrect\n")
        try:
            yield child
        finally:
            child.close()

    monkeypatch.setattr(native_preload, "open_worker", open_worker)
    return child


def test_preload_rejects_unpinned_runtime_before_start(tmp_path, monkeypatch):
    from core import paths, plugin_bootstrap, plugin_runtime_inventory
    root = tmp_path / "data"
    cache = root / "cache" / "plugin-bootstrap"
    cache.mkdir(parents=True)
    archive = cache / "python.zip"
    with zipfile.ZipFile(archive, "w") as item:
        item.writestr("python.exe", b"trusted interpreter")
        item.writestr("python.dll", b"trusted library")
    body = archive.read_bytes()
    wheel = cache / "sdk.whl"
    with zipfile.ZipFile(wheel, "w") as item:
        item.writestr("trusted_dependency.py", b"trusted dependency")
    wheel_body = wheel.read_bytes()
    monkeypatch.setattr(paths, "data_dir", lambda: root)
    monkeypatch.setattr(plugin_bootstrap, "_manifest", lambda: [{
        "kind": "python", "filename": archive.name, "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }, {"kind": "wheel", "filename": wheel.name, "size": len(wheel_body),
        "sha256": hashlib.sha256(wheel_body).hexdigest()}])
    monkeypatch.setattr(plugin_runtime_inventory, "snapshot", lambda: {"core/sdk.py": b"trusted sdk"})
    python = tmp_path / "stage" / "runtime" / "python"
    python.mkdir(parents=True)
    (python / "python.exe").write_bytes(b"trusted interpreter")
    (python / "python.dll").write_bytes(b"trusted library")
    site = python.parent / "site-packages"
    (site / "core").mkdir(parents=True)
    (site / "core" / "sdk.py").write_bytes(b"trusted sdk")
    (site / "trusted_dependency.py").write_bytes(b"trusted dependency")
    native_preload._verify_platform_runtime(tmp_path / "stage")
    monkeypatch.setattr(native_preload, "open_worker", lambda *a, **k: pytest.fail("untrusted interpreter launched"))
    for target, untrusted in ((python / "python.dll", b"modified library"),
                              (site / "core" / "sdk.py", b"modified sdk"),
                              (site / "trusted_dependency.py", b"modified dependency"),
                              (site / "injected.py", b"untrusted dependency")):
        old = target.read_bytes() if target.exists() else None
        target.write_bytes(untrusted)
        with pytest.raises(ValueError):
            with native_preload.open_preloaded_worker(None, tmp_path / "stage", deadline=time.monotonic() + 5):
                pytest.fail("untrusted runtime became ready")
        target.unlink() if old is None else target.write_bytes(old)


def test_activation_requires_checkpoint_and_is_one_shot(tmp_path, monkeypatch):
    child = _worker(monkeypatch)
    monkeypatch.setattr(native_preload, "exchange", lambda *a, **k: {"value": 7})
    calls = []
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
        result = worker.activate(deadline=time.monotonic() + 5, dispatch=None,
                                 checkpoint=lambda: calls.append("checked"), output=None)
        assert result == {"value": 7}
        assert calls
        assert re.fullmatch(rb"OPSKIT_ACTIVATE:[0-9a-f]{32}\n", child.stdin.received)
        with pytest.raises(RuntimeError, match="no longer available"):
            worker.activate(deadline=time.monotonic() + 5, dispatch=None, checkpoint=lambda: None, output=None)
    assert child.closed


def test_activation_passes_dynamic_deadline_to_exchange(tmp_path, monkeypatch):
    _worker(monkeypatch)
    deadline = [time.monotonic() + 5]
    def exchange(child, *, deadline, **kwargs):
        initial = deadline()
        kwargs["dispatch"]({})
        assert deadline() == initial + 60
        return {"value": 7}
    monkeypatch.setattr(native_preload, "exchange", exchange)
    def dispatch(message):
        deadline[0] += 60
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=deadline[0]) as worker:
        assert worker.activate(deadline=deadline[0], dispatch=dispatch, checkpoint=lambda: None,
                               output=None, protocol_deadline=lambda: deadline[0]) == {"value": 7}


def test_revoked_before_activation_sends_no_signal(tmp_path, monkeypatch):
    child = _worker(monkeypatch)
    def denied():
        raise PermissionError("revoked")
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
        with pytest.raises(PermissionError, match="revoked"):
            worker.activate(deadline=time.monotonic() + 5, dispatch=None, checkpoint=denied, output=None)
    assert not child.stdin.received
    assert child.closed


def test_activation_waits_for_sdk_readiness(tmp_path, monkeypatch):
    child = _worker(monkeypatch, sdk="pending")
    monkeypatch.setattr(native_preload, "exchange", lambda *a, **k: {"value": 7})
    checked = threading.Event()
    results = []
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
        def run():
            try:
                results.append(worker.activate(deadline=time.monotonic() + 5, dispatch=None,
                                               checkpoint=checked.set, output=None))
            except BaseException as error:
                results.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        assert checked.wait(3)
        assert worker.ready_at is None
        assert not child.stdin.received
        child.stdout.lines.put(("OPSKIT_READY:" + child.token + "\n").encode())
        thread.join(3)
        assert not thread.is_alive()
        assert results == [{"value": 7}]
        assert worker.ready_at is not None
        assert re.fullmatch(rb"OPSKIT_ACTIVATE:[0-9a-f]{32}\n", child.stdin.received)
    assert child.closed


@pytest.mark.parametrize("sdk", ["invalid", "eof", "pending"])
def test_sdk_failure_never_activates_plugin(tmp_path, monkeypatch, sdk):
    from core.plugin_entry_preload import PreloadStale
    child = _worker(monkeypatch, sdk=sdk)
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
        with pytest.raises(PreloadStale):
            worker.activate(deadline=time.monotonic() + .3, dispatch=None,
                            checkpoint=lambda: None, output=None)
        assert not child.stdin.received
    assert child.closed


def test_sdk_ready_after_startup_deadline_never_activates_plugin(tmp_path, monkeypatch):
    from core.plugin_entry_preload import PreloadStale
    child = _worker(monkeypatch, sdk="pending")
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + .2) as worker:
        time.sleep(.25)
        child.stdout.lines.put(("OPSKIT_READY:" + child.token + "\n").encode())
        with pytest.raises(PreloadStale):
            worker.activate(deadline=time.monotonic() + 2, dispatch=None,
                            checkpoint=lambda: None, output=None)
        assert not child.stdin.received
    assert child.closed


@pytest.mark.parametrize("response", ["invalid", "silent"])
def test_failed_readiness_retires_worker(tmp_path, monkeypatch, response):
    from core.plugin_entry_preload import PreloadStale
    child = _worker(monkeypatch, response=response)
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
        with pytest.raises(PreloadStale):
            worker.activate(deadline=time.monotonic() + .3, dispatch=None,
                            checkpoint=lambda: None, output=None)
        assert not child.stdin.received
    assert child.closed


def test_cancel_before_launch_starts_no_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(native_preload, "open_worker", lambda *a, **k: pytest.fail("worker launched"))
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5,
                                                 cancel_event=cancelled):
            pytest.fail("cancelled worker became ready")


def test_blocked_activation_is_reaped_after_deadline(tmp_path, monkeypatch):
    child = _worker(monkeypatch, blocked=True)
    with pytest.raises(TimeoutError):
        with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
            worker.activate(deadline=time.monotonic() + .3, dispatch=None, checkpoint=lambda: None, output=None)
    assert child.closed


def test_closing_active_preload_cancels_exchange(tmp_path, monkeypatch):
    child = _worker(monkeypatch)
    entered = threading.Event()
    failures = []
    def exchange(*args, checkpoint, **kwargs):
        entered.set()
        while True:
            checkpoint()
            time.sleep(.01)
    monkeypatch.setattr(native_preload, "exchange", exchange)
    with native_preload.open_preloaded_worker(None, tmp_path, deadline=time.monotonic() + 5) as worker:
        def run():
            try:
                worker.activate(deadline=time.monotonic() + 5, dispatch=None, checkpoint=lambda: None, output=None)
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        assert entered.wait(5)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], InterruptedError)
    assert child.closed
