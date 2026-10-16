"""Bounded file-backed binary capture with process-tree lifetime supervision."""
from contextlib import ExitStack, contextmanager
import math
import queue
import subprocess
import threading
import time

from core.process import start, terminate_tree
from core.private_file import temporary_private_file

MAX_RESPONSE_BYTES = 80 * 1024**2 + 1024
MAX_ERROR_BYTES = 65536


@contextmanager
def capture(command, request, *, timeout, max_bytes=MAX_RESPONSE_BYTES, request_limit=4096, cwd=None, env=None):
    """Yield private exact stdout only after successful exit and tree cleanup.

    Stderr is bounded and discarded. Neither child diagnostics nor response bytes
    enter exceptions. Callers still own cleanup of remote/daemon-side resources.
    """
    if (type(request_limit) is not int or not 0 < request_limit <= 131072
            or not isinstance(request, bytes) or len(request) > request_limit or type(max_bytes) is not int
            or not 0 < max_bytes <= MAX_RESPONSE_BYTES or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("invalid binary capture budget")
    deadline = time.monotonic() + timeout
    with temporary_private_file() as input_file, temporary_private_file() as output_file:
        input_file.write(request)
        input_file.seek(0)
        process = start(command, cwd=cwd, env=env, stdin=input_file, binary=True, stderr=subprocess.PIPE)
        pending = queue.Queue(maxsize=8)
        stopped = threading.Event()

        def put(item):
            while not stopped.is_set():
                try:
                    pending.put(item, timeout=0.05)
                    return
                except queue.Full:
                    pass

        def read(pipe, kind):
            try:
                while not stopped.is_set():
                    body = pipe.read1(65536)
                    if not body:
                        break
                    put((kind, body))
            except Exception:
                put(("error", b""))
            finally:
                put((kind, None))

        threads = [threading.Thread(target=read, args=(pipe, kind), daemon=True)
                   for pipe, kind in ((process.stdout, "out"), (process.stderr, "err"))]
        try:
            for thread in threads:
                thread.start()
            ended, sizes = set(), {"out": 0, "err": 0}
            while len(ended) != 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("binary capture deadline exceeded")
                try:
                    kind, body = pending.get(timeout=min(remaining, 0.05))
                except queue.Empty:
                    continue
                if kind == "error":
                    raise RuntimeError("binary capture pipe failed")
                if body is None:
                    ended.add(kind)
                    continue
                sizes[kind] += len(body)
                if sizes[kind] > (max_bytes if kind == "out" else MAX_ERROR_BYTES):
                    raise RuntimeError("binary capture output budget exceeded")
                if kind == "out":
                    output_file.write(body)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("binary capture deadline exceeded")
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise TimeoutError("binary capture deadline exceeded") from None
            if code:
                raise RuntimeError("binary capture command failed")
        finally:
            stopped.set()
            # Attempt every cleanup even if a pipe close or process reap fails.
            def close_reader(pipe, thread):
                # BufferedReader.close waits on a blocked read's lock. If tree
                # termination failed, retain that pipe rather than hang cleanup;
                # the propagated error requires caller-side resource recovery.
                if pipe is not None and not thread.is_alive():
                    pipe.close()
            with ExitStack() as cleanup:
                for pipe, thread in zip((process.stdout, process.stderr), threads):
                    cleanup.callback(close_reader, pipe, thread)
                for thread in threads:
                    if thread.ident is not None:
                        cleanup.callback(thread.join, timeout=3)
                cleanup.callback(terminate_tree, process)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("binary capture reader cleanup incomplete")
        output_file.seek(0)
        yield output_file
