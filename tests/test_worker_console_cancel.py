"""Console interruption belongs to the supervisor, not disposable helpers."""
import signal
import subprocess
import sys
import time

import pytest

from network.worker import owned_process, send, wait_result


def _interrupted_helper(connection):
    signal.raise_signal(signal.SIGINT)
    send(connection, {"supervisor_owns_cancellation": True})


def test_owned_helper_does_not_abort_or_print_traceback_on_console_interrupt(capfd):
    original = signal.getsignal(signal.SIGINT)
    with owned_process(_interrupted_helper, (), label="console cancellation test") as (receive, process):
        result = wait_result(receive, deadline=time.monotonic() + 10,
                             label="console cancellation test")
        assert result == {"supervisor_owns_cancellation": True}
    assert signal.getsignal(signal.SIGINT) == original
    assert "Traceback" not in capfd.readouterr().err

@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows console")
def test_windows_ctrl_c_during_worker_unpickling(tmp_path):
    script = tmp_path / "interrupt_worker.py"
    script.write_text(
        """
import ctypes
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from network.worker import owned_process, send, wait_result


def interrupt_during_unpickle():
    time.sleep(0.2)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for _ in range(3):
        if not kernel.GenerateConsoleCtrlEvent(0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.10)
    return True


class StartupProbe:
    def __reduce__(self):
        return interrupt_during_unpickle, ()


def complete(connection, probe):
    send(connection, {"unpickled": probe})


if __name__ == "__main__":
    received = []
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)

    @handler_type
    def on_control(event):
        if event != 0:
            return False
        received.append(event)
        return True

    if not kernel.SetConsoleCtrlHandler(on_control, True):
        raise ctypes.WinError(ctypes.get_last_error())
    with owned_process(complete, (StartupProbe(),), label="startup probe") as (receive, _):
        result = wait_result(receive, deadline=time.monotonic() + 10, label="startup probe")
    assert result == {"unpickled": True}, result
    assert received, "parent Ctrl+C handling must be restored after spawn"
""",
        encoding="utf-8",
    )
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run([sys.executable, str(script)], startupinfo=startup,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
