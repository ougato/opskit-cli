"""Bounded native worker exchange, independent of OS isolation implementation."""
from __future__ import annotations

import queue
import threading
import time

from core import plugin_json
from core.native_sandbox import NativeSandboxError

PREFIX = b"OPSKIT_RPC:"
LINE_LIMIT = 32 * 1024**2
OUTPUT_LIMIT = 64 * 1024**2


def exchange(child, *, deadline, dispatch, checkpoint, output):
    """Pump one worker to its terminal result; caller owns process termination.

    Reads and writes run in bounded queues/threads so a malicious peer cannot
    block the host's authorization checks by refusing to read or finish a line.
    """
    if not isinstance(deadline, (int, float)) or not time.monotonic() < deadline < float("inf"):
        raise ValueError("invalid native protocol deadline")
    incoming = queue.Queue(maxsize=4)
    outgoing = queue.Queue(maxsize=1)
    stopped = threading.Event()
    failures = queue.Queue(maxsize=2)

    def deliver(item):
        while not stopped.is_set():
            try:
                incoming.put(item, timeout=0.05)
                return
            except queue.Full:
                pass

    def reader():
        try:
            while not stopped.is_set():
                line = child.stdout.readline(LINE_LIMIT + 1)
                if not line:
                    break
                deliver(line)
                if len(line) > LINE_LIMIT:
                    break
        except (OSError, ValueError):
            failures.put("read")
        finally:
            deliver(None)

    def writer():
        while not stopped.is_set():
            try:
                data = outgoing.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                view = memoryview(data)
                while view and not stopped.is_set():
                    written = child.stdin.write(view)
                    if not written:
                        raise OSError("worker input closed")
                    view = view[written:]
                child.stdin.flush()
            except (OSError, ValueError):
                failures.put("write")
                return

    threads = [threading.Thread(target=reader, daemon=True), threading.Thread(target=writer, daemon=True)]
    for thread in threads:
        thread.start()
    result = None
    total = 0
    try:
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise NativeSandboxError("execution_timeout")
            if not failures.empty():
                raise NativeSandboxError("protocol_failed")
            try:
                line = incoming.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                break
            total += len(line)
            if len(line) > LINE_LIMIT or total > OUTPUT_LIMIT:
                raise NativeSandboxError("output_limit")
            if not line.startswith(PREFIX):
                output(line.decode("utf-8", errors="replace").rstrip())
                continue
            try:
                message = plugin_json.loads_message(line[len(PREFIX):].decode("utf-8", errors="strict"))
                if not isinstance(message, dict):
                    raise ValueError("invalid message")
                if message.get("type") == "result":
                    if result is not None:
                        raise ValueError("duplicate result")
                    result = plugin_json.require_result(message)
                elif message.get("type") == "call":
                    plugin_json.require_version(message)
                    if result is not None:
                        raise ValueError("call after result")
                    reply = dispatch(message)
                    checkpoint()
                    encoded = (plugin_json.dumps_message(reply) + "\n").encode("utf-8")
                    plugin_json.require_reply(plugin_json.loads_message(encoded.decode("utf-8")))
                    if len(encoded) > LINE_LIMIT:
                        raise ValueError("oversized reply")
                    try:
                        outgoing.put_nowait(encoded)
                    except queue.Full:
                        raise ValueError("worker did not consume prior reply") from None
                else:
                    raise ValueError("invalid message type")
            except (ValueError, UnicodeError, TypeError, RecursionError) as error:
                raise NativeSandboxError("protocol_failed") from error
        checkpoint()
        if result is None or result.get("error"):
            raise NativeSandboxError("worker_failed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeSandboxError("execution_timeout")
        if child.wait(remaining) != 0:
            raise NativeSandboxError("worker_failed")
        return result
    finally:
        stopped.set()
        # Process context kills the peer to unblock OS pipe calls; never wait
        # here while a malicious child still owns a pipe endpoint.
        child._native_io_threads = threads


def join_io(child):
    """Call after the backend has killed the worker and closed child endpoints."""
    for thread in getattr(child, "_native_io_threads", ()):
        thread.join(timeout=3)
        if thread.is_alive():
            raise NativeSandboxError("transport_cleanup_failed")
