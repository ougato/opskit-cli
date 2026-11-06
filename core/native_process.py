"""Host-owned pipe transport shared by native isolation backends."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import os


class _PythonStartupOutput:
    """Consume only CPython's nonfatal executable-realpath startup warning.

    CPython can fail to resolve the executable's final path in a restricted
    Windows token even though the verified executable is usable. stderr and
    stdout share this pipe, so this warning otherwise corrupts readiness and
    appears as plugin output. Never discard any other worker output.
    """

    def __init__(self, stream, executable):
        self._stream = stream
        # CPython's getpath warning writes PyUnicode_AsUTF8 to stderr.
        self._expected = b"Failed to find real location of " + executable.encode("utf-8")
        self._pending = None

    def readline(self, limit=-1):
        if limit == 0:
            return b""
        if self._pending is None:
            first = self._stream.readline(len(self._expected) + 3)
            if first.rstrip(b"\r\n") == self._expected and first.endswith(b"\n"):
                from core.logger import get_logger
                get_logger("opskit.plugin.runtime").debug(
                    "Isolated Python executable realpath unavailable during startup")
                self._pending = b""
            else:
                self._pending = first
        if self._pending:
            size = len(self._pending) if limit is None or limit < 0 else min(limit, len(self._pending))
            head, self._pending = self._pending[:size], self._pending[size:]
            if head.endswith(b"\n") or self._pending or (limit is not None and 0 <= limit <= len(head)):
                return head
            remaining = -1 if limit is None or limit < 0 else limit - len(head)
            return head + self._stream.readline(remaining)
        return self._stream.readline(limit)


class WorkerPipes:
    """Only the worker's input/output endpoints are inheritable via its launcher."""

    def __init__(self):
        self._stack = ExitStack()
        self._child = ExitStack()
        try:
            child_read, parent_write = os.pipe()
            self._child.callback(os.close, child_read)
            self._stack.callback(os.close, parent_write)
            parent_read, child_write = os.pipe()
            self._child.callback(os.close, child_write)
            self._stack.callback(os.close, parent_read)
            # Move parent descriptors to file ownership only after all setup succeeds.
            self.stdin = os.fdopen(os.dup(parent_write), "wb", buffering=0)
            self._stack.enter_context(self.stdin)
            self.stdout = os.fdopen(os.dup(parent_read), "rb", buffering=0)
            self._stack.enter_context(self.stdout)
            self._child_fds = (child_read, child_write, child_write)
        except BaseException:
            self.close()
            raise

    def child_handles(self):
        if os.name == "nt":
            import msvcrt
            return tuple(msvcrt.get_osfhandle(fd) for fd in self._child_fds)
        return self._child_fds

    def release_child_ends(self):
        self._child.close()

    def close(self):
        try:
            self._child.close()
        finally:
            self._stack.close()


@contextmanager
def open_worker(backend, executable, args, *, filter_python_startup=False, **options):
    """Yield process + binary stdin/stdout. Backend exit kills descendants first."""
    from core.i18n import current_lang
    environment = dict(options.pop("environment", None) or {})
    environment["OPSKIT_LANGUAGE"] = current_lang()
    options["environment"] = environment
    pipes = WorkerPipes()
    try:
        with backend.launch(executable, args, stdio=pipes.child_handles(), **options) as process:
            pipes.release_child_ends()
            process.stdin = pipes.stdin
            process.stdout = (_PythonStartupOutput(pipes.stdout, executable)
                              if filter_python_startup and os.name == "nt" else pipes.stdout)
            yield process
    finally:
        try:
            if "process" in locals():
                from core.native_protocol import join_io
                join_io(process)
        finally:
            pipes.close()
