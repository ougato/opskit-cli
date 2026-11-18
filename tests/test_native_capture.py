"""Transport tests use real supervised pipes; isolation is tested separately."""
from contextlib import contextmanager
import subprocess
import sys
import time

import pytest

from core.native_capture import capture
from core.native_protocol import join_io
from core.native_sandbox import NativeSandboxError
from core.private_file import temporary_private_file


@contextmanager
def child(code):
    process = subprocess.Popen([sys.executable, "-I", "-S", "-u", "-c", code],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        join_io(process)
        process.stdout.close()


def test_capture_preserves_binary_bytes():
    with child("import sys; sys.stdout.buffer.write(b'\\x00hello\\xff')") as process:
        with temporary_private_file() as result:
            assert capture(process, result, deadline=time.monotonic() + 5, checkpoint=lambda: None) == 7
            assert result.read() == b"\x00hello\xff"


def test_capture_rejects_overflow_and_cleans_reader():
    with child("import sys; sys.stdout.buffer.write(b'x' * 65536)") as process:
        with temporary_private_file() as result:
            with pytest.raises(NativeSandboxError) as error:
                capture(process, result, deadline=time.monotonic() + 5,
                        checkpoint=lambda: None, max_bytes=100)
            assert error.value.code == "output_limit"


def test_capture_checks_revocation_during_silence():
    start = time.monotonic()
    def check():
        if time.monotonic() - start > .15:
            raise PermissionError("revoked")
    with child("import time; time.sleep(60)") as process:
        with temporary_private_file() as result:
            with pytest.raises(PermissionError, match="revoked"):
                capture(process, result, deadline=start + 5, checkpoint=check)
    assert time.monotonic() - start < 5


def test_capture_rejects_nonzero_exit_without_exposing_output():
    with child("print('secret'); raise SystemExit(7)") as process:
        with temporary_private_file() as result:
            with pytest.raises(NativeSandboxError) as error:
                capture(process, result, deadline=time.monotonic() + 5, checkpoint=lambda: None)
            assert error.value.code == "worker_failed"
            assert "secret" not in str(error.value)


def test_capture_silence_cannot_extend_expired_execution_budget():
    start = time.monotonic()
    with child("import time; time.sleep(60)") as process:
        with temporary_private_file() as result:
            with pytest.raises(NativeSandboxError) as error:
                capture(process, result, deadline=start + .3, checkpoint=lambda: None)
            assert error.value.code == "execution_timeout"
    assert time.monotonic() - start < 5
