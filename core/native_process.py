"""Host-owned pipe transport shared by native isolation backends."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import os


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
def open_worker(backend, executable, args, **options):
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
            process.stdout = pipes.stdout
            yield process
    finally:
        try:
            if "process" in locals():
                from core.native_protocol import join_io
                join_io(process)
        finally:
            pipes.close()
