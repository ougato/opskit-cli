from io import BytesIO
import pytest

from core.native_process import _PythonStartupOutput


EXECUTABLE = r"\\?\F:\opskit\native-call\runtime\python\python.exe"
WARNING = b"Failed to find real location of " + EXECUTABLE.encode("utf-8") + b"\r\n"


def test_nonfatal_interpreter_warning_does_not_interrupt_readiness():
    stream = _PythonStartupOutput(BytesIO(WARNING + b"OPSKIT_READY:token\r\n"
                                          b"later worker error\r\n"), EXECUTABLE)
    assert stream.readline(128) == b"OPSKIT_READY:token\r\n"
    assert stream.readline(128) == b"later worker error\r\n"
    assert stream.readline(128) == b""


@pytest.mark.parametrize("prefix", [
    b"another interpreter error\r\n",
    b"Failed to find real location of C:\\other\\python.exe\r\n",
    WARNING.rstrip(b"\r\n") + b" extra\r\n",
    WARNING.rstrip(b"\r\n"),
])
def test_other_or_incomplete_diagnostics_are_preserved(prefix):
    stream = _PythonStartupOutput(BytesIO(prefix), EXECUTABLE)
    assert stream.readline(4096) == prefix


def test_first_long_protocol_line_obeys_readline_limits():
    line = b"OPSKIT_RPC:" + b"x" * 1000 + b"\n"
    stream = _PythonStartupOutput(BytesIO(line + b"next\n"), EXECUTABLE)
    assert stream.readline(0) == b""
    assert stream.readline(512) == line[:512]
    assert stream.readline(2048) == line[512:]
    assert stream.readline(2048) == b"next\n"
