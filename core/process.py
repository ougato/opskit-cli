"""Process supervision with bounded buffering and wall-clock deadlines."""
from __future__ import annotations

import os
import math
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
        from core.windows_job_lifecycle import terminate_and_wait
        try:
            terminate_and_wait(job)
        finally:
            try:
                close(job)
            finally:
                process._opskit_job = None
                process.wait(timeout=3)
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as denied:
            # Allow one bounded wait for our child after a denied signal,
            # then retry the group so surviving
            # descendants are still killed. Never suppress a persistent denial.
            if process.poll() is None:
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    raise denied from None
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


def start(cmd: Sequence[str], *, cwd=None, env=None, stdin=None, binary=False, stderr=subprocess.STDOUT):
    """Start a text client in a group/kill-on-close Job before it can fork."""
    process = subprocess.Popen(
        list(cmd), cwd=str(cwd) if cwd else None, env=env,
        stdin=stdin,
        stdout=subprocess.PIPE, stderr=stderr,
        text=not binary, encoding=None if binary else "utf-8", errors=None if binary else "replace",
        start_new_session=os.name != "nt",
        creationflags=0x4 if os.name == "nt" else 0,
    )
    if os.name == "nt":
        from core.windows_job import attach_and_resume
        try:
            process._opskit_job = attach_and_resume(process)
        except BaseException:
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            raise
    return process


def stream(cmd: Sequence[str], *, cwd=None, env=None, timeout=None, process_ref=None, stdin=None,
           stdin_data=None, checkpoint=None, fallback_encoding=None) -> Iterator[str]:
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("process timeout must be finite and positive")
    if stdin_data is not None and (not isinstance(stdin_data, bytes) or len(stdin_data) > 16384 or stdin is not None):
        raise ValueError("invalid process input")
    deadline = time.monotonic() + timeout if timeout is not None else None
    if checkpoint is not None:
        checkpoint()
    process = start(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_data is not None else stdin,
                    **({"binary": True} if fallback_encoding else {}))
    if process_ref is not None:
        process_ref.append(process)
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
    def writer() -> None:
        assert process.stdin is not None
        try:
            (process.stdin if fallback_encoding else process.stdin.buffer).write(stdin_data)
            process.stdin.buffer.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    input_thread = threading.Thread(target=writer, daemon=True) if stdin_data is not None else None
    tree_stopped = False
    try:
        thread.start()
        if input_thread is not None:
            input_thread.start()
        while True:
            if checkpoint is not None:
                checkpoint()
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(list(cmd), timeout)
            try:
                chunk = output.get(timeout=min(remaining, 0.1) if remaining is not None else 0.1)
            except queue.Empty:
                # A completed leader can leave descendants holding stdout open.
                # Stop the owned tree, then drain remaining output to EOF rather
                # than waiting until the whole operation's deadline expires.
                if not tree_stopped and process.poll() is not None:
                    terminate_tree(process)
                    tree_stopped = True
                continue
            if chunk is None:
                break
            if not fallback_encoding:
                yield chunk.rstrip("\n")
                continue
            # Match universal newlines in text mode, including Creator's
            # standalone carriage return between a marker and its JSON.
            lines = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            for line in lines.removesuffix(b"\n").split(b"\n"):
                try:
                    yield line.decode("utf-8")
                except UnicodeDecodeError:
                    yield line.decode(fallback_encoding, errors="replace")
        remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
        while process.poll() is None:
            if checkpoint is not None:
                checkpoint()
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(list(cmd), timeout)
            try:
                process.wait(timeout=min(remaining, 0.1) if remaining is not None else 0.1)
            except subprocess.TimeoutExpired:
                continue
        if checkpoint is not None:
            checkpoint()
        return process.returncode
    finally:
        stopped.set()
        terminate_tree(process)
        if thread.ident is not None:
            thread.join(timeout=3)
        if input_thread is not None and input_thread.ident is not None:
            input_thread.join(timeout=3)
        if not thread.is_alive() and process.stdout is not None:
            process.stdout.close()


def checked_output(command, *, timeout, stdin=subprocess.DEVNULL, max_bytes=4096):
    """Bounded command output; errors never include captured diagnostics."""
    result = capture(command, timeout=timeout, stdin=stdin, max_bytes=max_bytes)
    if result.returncode:
        raise RuntimeError("command failed")
    return result.stdout.strip()


def capture(cmd: Sequence[str], *, timeout, max_bytes=65536, env=None, stdin=None, cwd=None):
    """Capture bounded merged text output using the same process-tree supervisor.

    Intended for small diagnostic command results, rather than binary or exact
    byte output. Does not raise for a nonzero exit status.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("output limit must be a positive integer")
    iterator = stream(cmd, timeout=timeout, env=env, stdin=stdin, cwd=cwd)
    lines = []
    size = 0
    try:
        while True:
            try:
                line = next(iterator) + "\n"
            except StopIteration as done:
                return subprocess.CompletedProcess(list(cmd), done.value, "".join(lines), "")
            size += len(line.encode("utf-8"))
            if size > max_bytes:
                raise RuntimeError("diagnostic command output exceeds limit")
            lines.append(line)
    except subprocess.TimeoutExpired as exc:
        exc.output = "".join(lines)
        raise
    finally:
        iterator.close()
