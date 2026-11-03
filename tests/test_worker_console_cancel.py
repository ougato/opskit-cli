"""Console interruption belongs to the supervisor, not disposable helpers."""
import signal
import time

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
