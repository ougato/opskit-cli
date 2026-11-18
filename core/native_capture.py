"""Bounded binary output from an already isolated native process."""
import queue
import threading
import time

from core.native_sandbox import NativeSandboxError

MAX_BYTES = 80 * 1024**2 + 1024


def capture(child, destination, *, deadline, checkpoint, max_bytes=MAX_BYTES):
    """Drain to a host-private file; caller must kill the child before joining IO.

    The worker reads a staged request file instead of consuming pipe EOF. Nothing
    from this stream is rendered as a diagnostic or accepted before a zero exit.
    """
    if (type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES
            or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not time.monotonic() < deadline < float("inf")):
        raise ValueError("invalid native capture budget")
    incoming = queue.Queue(maxsize=4)
    stopped = threading.Event()

    def deliver(item):
        while not stopped.is_set():
            try:
                incoming.put(item, timeout=.05)
                return
            except queue.Full:
                pass

    def read():
        try:
            while not stopped.is_set():
                body = child.stdout.read(65536)
                if not body:
                    break
                deliver(body)
        except (OSError, ValueError):
            deliver(False)
        finally:
            deliver(None)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    total = 0
    try:
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise NativeSandboxError("execution_timeout")
            try:
                body = incoming.get(timeout=.05)
            except queue.Empty:
                continue
            if body is None:
                break
            if body is False:
                raise NativeSandboxError("protocol_failed")
            total += len(body)
            if total > max_bytes:
                raise NativeSandboxError("output_limit")
            destination.write(body)
        # EOF alone does not prove termination. Keep checking authorization even
        # when a peer closes stdout and remains alive.
        while child.poll() is None:
            checkpoint()
            if time.monotonic() >= deadline:
                raise NativeSandboxError("execution_timeout")
            stopped.wait(.05)
        checkpoint()
        if child.poll() != 0:
            raise NativeSandboxError("worker_failed")
        destination.seek(0)
        return total
    finally:
        stopped.set()
        child._native_io_threads = [thread]
