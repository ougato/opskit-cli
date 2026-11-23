"""Bounded JSON messages and disposable processes for blocking network calls."""
import json
import math
import multiprocessing
import os
import sys
import threading
import time
from contextlib import ExitStack, contextmanager


_windows_spawn_lock = threading.Lock()


def _start_process(process):
    if os.name != "nt":
        process.start()
        return

    import ctypes
    from ctypes import wintypes

    set_handler = ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleCtrlHandler
    set_handler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    set_handler.restype = wintypes.BOOL
    with _windows_spawn_lock:
        # Children inherit this flag across interpreter startup and unpickling.
        # A console-free host has no broadcast to suppress.
        changed = set_handler(None, True)
        try:
            process.start()
        finally:
            if changed:
                set_handler(None, False)


def send(connection, message):
    encoded = json.dumps(message).encode("utf-8")
    if len(encoded) > 65536:
        raise ValueError("network worker result exceeds limit")
    connection.send_bytes(encoded)


def _owned_target(connection, lifetime, ready, parent_pid, target, arguments):
    # The supervisor owns interactive cancellation and reaps this process.
    # Windows broadcasts console Ctrl+C to attached helper processes too;
    # do not let an idle helper print a misleading multiprocessing traceback.
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "worker parent-death protection failed")
        if os.getppid() != parent_pid:
            os._exit(1)
    def parent_closed():
        try:
            lifetime.recv_bytes(1)
        except (EOFError, OSError):
            pass
        os._exit(1)
    threading.Thread(target=parent_closed, daemon=True, name="worker-parent-lifetime").start()
    try:
        if ready.recv_bytes(1) != b'R':
            raise RuntimeError("invalid worker startup gate")
    finally:
        ready.close()
    target(connection, *arguments)


@contextmanager
def owned_process(target, arguments, *, label):
    """One ownership implementation for disposable and invocation workers."""
    context = multiprocessing.get_context("spawn")
    receive = transmit = lifetime_read = lifetime_write = process = None
    ready_read = ready_write = None
    job = None
    try:
        receive, transmit = context.Pipe(duplex=False)
        lifetime_read, lifetime_write = context.Pipe(duplex=False)
        ready_read, ready_write = context.Pipe(duplex=False)
        process = context.Process(target=_owned_target,
                                  args=(transmit, lifetime_read, ready_read, os.getpid(), target, arguments), daemon=True)
        _start_process(process)
        if os.name == "nt":
            from core.windows_job import attach_running
            job = attach_running(process.sentinel)
        ready_read.close()
        ready_write.send_bytes(b'R')
        ready_write.close()
        transmit.close()
        lifetime_read.close()
        yield receive, process
    finally:
        # Caught exceptions may retain this generator frame until a later GC.
        # The child has its own serialized arguments; retire parent references.
        arguments = ()
        try:
            # Attempt every close even when one pipe reports an error. Such an
            # error must not bypass the Job and process retirement below.
            with ExitStack() as connections:
                for connection in reversed((receive, transmit, lifetime_read, lifetime_write, ready_read, ready_write)):
                    if connection is not None:
                        connections.callback(connection.close)
        finally:
            try:
                if job is not None:
                    from core.windows_job import close
                    close(job)
            finally:
                if process is not None and process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=1)
                    if process.is_alive():
                        raise RuntimeError(label + " worker cleanup failed")
                    process.close()


def wait_result(receive, *, deadline, label, cancel_event=None, on_deadline=None, on_cancel=None):
    """Wait under an existing deadline; never restart a caller's allowance."""
    latest = None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            if on_cancel is not None:
                return on_cancel(latest)
            raise InterruptedError("network operation cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if on_deadline is not None:
                return on_deadline(latest)
            raise TimeoutError(label + " deadline exceeded")
        if not receive.poll(min(remaining, .1)):
            continue
        try:
            message = json.loads(receive.recv_bytes(65536))
            if not isinstance(message, dict):
                raise ValueError()
        except (EOFError, OSError, ValueError):
            raise RuntimeError(label + " worker failed") from None
        if time.monotonic() >= deadline:
            if on_deadline is not None:
                return on_deadline(latest)
            raise TimeoutError(label + " deadline exceeded")
        if "progress" in message:
            latest = message["progress"]
            continue
        return message


def run(target, arguments, *, timeout, label, cancel_event=None, on_deadline=None, on_cancel=None):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("network timeout must be positive")
    deadline = time.monotonic() + timeout
    if cancel_event is not None and cancel_event.is_set():
        if on_cancel is not None:
            return on_cancel(None)
        raise InterruptedError("network operation cancelled")
    with owned_process(target, arguments, label=label) as (receive, _):
        return wait_result(receive, deadline=deadline, label=label, cancel_event=cancel_event,
                           on_deadline=on_deadline, on_cancel=on_cancel)
