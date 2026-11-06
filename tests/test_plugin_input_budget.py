from types import SimpleNamespace

import pytest

from core import plugin_sandbox, prompt


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows terminal input")
def test_terminal_wait_does_not_consume_execution_budget(monkeypatch):
    import msvcrt
    now = [100.0]
    checks = []
    monkeypatch.setattr(plugin_sandbox, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(prompt, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(prompt, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(plugin_sandbox._invocations, "deadline", 110.0, raising=False)
    monkeypatch.setattr(plugin_sandbox._invocations, "input_wait_tick", None, raising=False)
    monkeypatch.setattr(plugin_sandbox._invocations, "input_wait_total", 0.0, raising=False)
    def ready():
        now[0] += 60
        return now[0] >= 400
    monkeypatch.setattr(msvcrt, "kbhit", ready)
    monkeypatch.setattr(msvcrt, "getwch", lambda: "1")
    def check():
        checks.append(now[0])
        plugin_sandbox._remaining()
    with prompt.input_wait_scope(plugin_sandbox._input_wait_budget), prompt.guard_input(check):
        assert prompt._getwch() == "1"
    assert len(checks) >= 5
    assert plugin_sandbox._remaining() == 10
    now[0] += 11
    with pytest.raises(plugin_sandbox.SandboxError, match="deadline"):
        plugin_sandbox._remaining()


def test_revocation_still_interrupts_terminal_wait(monkeypatch):
    monkeypatch.setattr(plugin_sandbox._invocations, "deadline", plugin_sandbox.time.monotonic() + 10, raising=False)
    monkeypatch.setattr(plugin_sandbox._invocations, "input_wait_tick", None, raising=False)
    with pytest.raises(PermissionError):
        with plugin_sandbox._input_wait_budget():
            raise PermissionError("revoked")
    assert plugin_sandbox._invocations.input_wait_tick is None


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows terminal input")
def test_queued_key_is_read_even_when_kbhit_misses_it(monkeypatch):
    import msvcrt
    from core import windows_console_input

    checks = []
    monkeypatch.setattr(prompt, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(msvcrt, "kbhit", lambda: False)
    monkeypatch.setattr(windows_console_input, "key_available", lambda: True)
    monkeypatch.setattr(msvcrt, "getwch", lambda: "\r")
    with prompt.guard_input(lambda: checks.append(True)):
        assert prompt._getwch() == "\r"
    assert checks


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows terminal input")
def test_guard_revocation_interrupts_key_polling(monkeypatch):
    import msvcrt
    from core import windows_console_input

    monkeypatch.setattr(prompt, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(msvcrt, "kbhit", lambda: False)
    monkeypatch.setattr(windows_console_input, "key_available", lambda: False)
    monkeypatch.setattr(prompt.time, "sleep", lambda _: None)
    checks = iter([None, None, PermissionError("revoked")])

    def check():
        result = next(checks)
        if result:
            raise result

    with prompt.guard_input(check), pytest.raises(PermissionError, match="revoked"):
        prompt._getwch()
