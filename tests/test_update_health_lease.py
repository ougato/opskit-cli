"""Explore an unintegrated lease primitive using disposable processes.

These tests do not establish an implemented startup/health protocol.
"""
from contextlib import contextmanager
import multiprocessing
import threading

import pytest

from core.resource_lock import ResourceBusyError, ResourceLock


class HealthLease:
    """Test-only prototype: own the ResourceLock on one worker thread."""

    def __init__(self, target):
        from core.updater import _update_target_lock_path

        self._resource = ResourceLock(
            lambda: _update_target_lock_path(target).with_suffix(".health.lock"),
            label="update health prototype",
        )
        self._ready = threading.Event()
        self._release = threading.Event()
        self._closed = threading.Event()
        self._failure = None
        self._thread = None

    def acquire(self, timeout=10.0):
        if timeout <= 0:
            raise ValueError("health lease startup timeout must be positive")
        if self._thread is not None:
            raise RuntimeError("health lease has already been started")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self._release.set()
            raise TimeoutError("health lease startup timed out")
        if self._failure is not None:
            raise self._failure

    def release(self, timeout=10.0):
        if self._thread is None:
            raise RuntimeError("health lease has not been started")
        self._release.set()
        if not self._closed.wait(timeout):
            raise TimeoutError("health lease release timed out")
        if self._failure is not None:
            raise self._failure

    def _run(self):
        try:
            with self._resource.hold():
                self._ready.set()
                self._release.wait()
        except BaseException as exc:
            self._failure = exc
            self._ready.set()
        finally:
            self._closed.set()


def _try_lease(target, result):
    lease = HealthLease(target)
    try:
        lease.acquire()
    except Exception as exc:
        result.send(type(exc).__name__)
    else:
        lease.release()
        result.send("acquired")
    finally:
        result.close()


def _hold_lease(target, ready):
    lease = HealthLease(target)
    lease.acquire()
    ready.set()
    # The parent terminates this owned fixture process to exercise OS release.
    threading.Event().wait(30)


def _check_in_spawned_process(target):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_try_lease, args=(target, send))
    process.start()
    send.close()
    try:
        if not receive.poll(15):
            raise RuntimeError("isolated health lease process did not respond")
        return receive.recv()
    finally:
        receive.close()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_same_target_is_exclusive_until_release(tmp_path):
    target = tmp_path / "opskit"
    target.write_bytes(b"test executable")
    lease = HealthLease(target)
    lease.acquire()
    try:
        assert _check_in_spawned_process(target) == ResourceBusyError.__name__
    finally:
        lease.release()
    assert _check_in_spawned_process(target) == "acquired"


def test_different_targets_do_not_share_a_lease(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    lease = HealthLease(first)
    lease.acquire()
    try:
        assert _check_in_spawned_process(second) == "acquired"
    finally:
        lease.release()


def test_process_exit_releases_owned_lease(tmp_path):
    target = tmp_path / "opskit"
    target.write_bytes(b"test executable")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_lease, args=(target, ready))
    process.start()
    try:
        if not ready.wait(15):
            raise RuntimeError("isolated lease holder did not start")
        assert _check_in_spawned_process(target) == ResourceBusyError.__name__
    finally:
        if process.is_alive():
            process.terminate()
        process.join(10)
    assert not process.is_alive()
    assert _check_in_spawned_process(target) == "acquired"


def test_late_acquisition_after_timeout_is_released_on_worker_thread(tmp_path):
    allow = threading.Event()
    acquired = threading.Event()
    ended = threading.Event()
    thread_ids = []

    @contextmanager
    def hold():
        if not allow.wait(5):
            raise RuntimeError("test did not release delayed acquisition")
        thread_ids.append(threading.get_ident())
        acquired.set()
        try:
            yield
        finally:
            thread_ids.append(threading.get_ident())
            ended.set()

    lease = HealthLease(tmp_path / "target")
    lease._resource.hold = hold
    try:
        with pytest.raises(TimeoutError):
            lease.acquire(timeout=0.05)
    finally:
        allow.set()
    assert acquired.wait(5)
    assert ended.wait(5)
    assert thread_ids[0] == thread_ids[1]
