"""Process supervision with bounded buffering and wall-clock deadlines."""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence


def terminate_tree(process: subprocess.Popen) -> None:
    job = getattr(process, "_opskit_job", None)
    if job is not None:
        from core.windows_job import close
        close(job)
        process._opskit_job = None
        process.wait(timeout=3)
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        import psutil
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
            psutil.wait_procs(children, timeout=3)
        except psutil.NoSuchProcess:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def stream(cmd: Sequence[str], *, cwd=None, env=None, timeout=None) -> Iterator[str]:
    process = subprocess.Popen(
        list(cmd), cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        start_new_session=os.name != "nt",
        creationflags=0x4 if os.name == "nt" else 0,
    )
    if os.name == "nt":
        from core.windows_job import attach_and_resume
        process._opskit_job = attach_and_resume(process)
    output: queue.Queue = queue.Queue(maxsize=32)
    stopped = threading.Event()

    def put(value) -> None:
        while not stopped.is_set():
            try:
                output.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def reader() -> None:
        assert process.stdout is not None
        try:
            while not stopped.is_set():
                chunk = process.stdout.readline(65536)
                if not chunk:
                    break
                put(chunk)
        finally:
            put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(list(cmd), timeout)
            try:
                chunk = output.get(timeout=min(remaining, 0.1) if remaining is not None else 0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break
            yield chunk.rstrip("\n")
        remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
        return process.wait(timeout=remaining)
    finally:
        stopped.set()
        terminate_tree(process)
        thread.join(timeout=3)
        if not thread.is_alive() and process.stdout is not None:
            process.stdout.close()
