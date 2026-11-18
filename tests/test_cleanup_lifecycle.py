"""Process cleanup and normal interactive exit retain their ownership boundaries."""
from __future__ import annotations

import gc
import os
import signal
import subprocess
import sys
import threading
import weakref
from types import SimpleNamespace

import pytest

from core import cleanup


@pytest.fixture
def isolated_cleanup(monkeypatch):
    monkeypatch.setattr(cleanup, "_callbacks", [])
    monkeypatch.setattr(cleanup, "_running", False)


def test_cleanup_reentrancy_exception_and_reverse_order(isolated_cleanup, caplog):
    calls = []

    def failing():
        calls.append("failing")
        cleanup._run_callbacks()
        raise RuntimeError("private detail")

    cleanup.register_cleanup(lambda: calls.append("first"))
    cleanup.register_cleanup(failing)
    cleanup.register_cleanup(lambda: calls.append("last"))
    cleanup._run_callbacks()
    cleanup._run_callbacks()

    assert calls == ["last", "failing", "first"]
    assert "RuntimeError" in caplog.text
    assert "private detail" not in caplog.text


def test_concurrent_cleanup_does_not_invoke_callback_twice(isolated_cleanup):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def callback():
        calls.append("run")
        entered.set()
        assert release.wait(5)

    cleanup.register_cleanup(callback)
    worker = threading.Thread(target=cleanup._run_callbacks)
    worker.start()
    try:
        assert entered.wait(5)
        cleanup._run_callbacks()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert calls == ["run"]


def test_expired_deadline_retains_unstarted_callbacks(isolated_cleanup, monkeypatch):
    times = iter([0, 0, 10])
    monkeypatch.setattr(cleanup.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(cleanup, "TIMEOUT_CLEANUP", 5)
    calls = []
    cleanup.register_cleanup(lambda: calls.append("first"))
    cleanup.register_cleanup(lambda: calls.append("second"))
    cleanup._run_callbacks()
    assert calls == ["second"]

    monkeypatch.setattr(cleanup.time, "monotonic", lambda: 0)
    cleanup._run_callbacks()
    assert calls == ["second", "first"]


@pytest.mark.parametrize("finish", ["normal", "exit", "signal"])
def test_process_exit_runs_callbacks_once(tmp_path, finish):
    marker = tmp_path / "callbacks.txt"
    script = """
import pathlib, signal, sys
from core import cleanup
path = pathlib.Path(sys.argv[1])
def callback():
    with path.open('a', encoding='utf-8') as stream:
        stream.write('done\\n')
cleanup.register_cleanup(callback)
cleanup.init()
if sys.argv[2] == 'exit':
    sys.exit(0)
if sys.argv[2] == 'signal':
    cleanup._signal_handler(signal.SIGTERM, None)
"""
    result = subprocess.run([sys.executable, "-c", script, str(marker), finish],
                            cwd=os.getcwd(), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "done\n"


def test_worker_init_does_not_prevent_main_thread_signal_registration():
    script = """
import signal, threading
from core import cleanup
worker = threading.Thread(target=cleanup.init)
worker.start()
worker.join()
assert not cleanup._registered
cleanup.init()
assert cleanup._registered
assert signal.getsignal(signal.SIGTERM) is cleanup._signal_handler
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=os.getcwd(),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX signal delivery")
@pytest.mark.parametrize("signame,expected_code", [("SIGINT", -signal.SIGINT), ("SIGTERM", 0), ("SIGHUP", 0)])
def test_real_posix_signal_runs_cleanup_once(tmp_path, signame, expected_code):
    marker = tmp_path / "signal-cleanup.txt"
    script = """
import os, pathlib, signal, sys, threading
from core import cleanup
path = pathlib.Path(sys.argv[1])
def record():
    with path.open('a', encoding='utf-8') as stream:
        stream.write('done\\n')
cleanup.register_cleanup(record)
worker = threading.Thread(target=cleanup.init)
worker.start()
worker.join()
cleanup.init()
os.kill(os.getpid(), getattr(signal, sys.argv[2]))
raise RuntimeError('signal was not delivered')
"""
    result = subprocess.run([sys.executable, "-c", script, str(marker), signame],
                            cwd=os.getcwd(), capture_output=True, text=True, timeout=15)
    assert result.returncode == expected_code, (result.returncode, result.stderr)
    assert marker.read_text(encoding="utf-8") == "done\n"


def test_init_retries_only_failed_signal_registration(monkeypatch):
    registrations = []
    attempts = []
    monkeypatch.setattr(cleanup, "_registered", False)
    monkeypatch.setattr(cleanup, "_atexit_registered", False)
    monkeypatch.setattr(cleanup, "_registered_signals", set())
    monkeypatch.setattr(cleanup.atexit, "register", lambda fn: registrations.append(fn))
    monkeypatch.setattr(cleanup, "_init_windows", lambda: None)

    def install(signum, handler):
        attempts.append(signum)
        if signum == signal.SIGTERM and attempts.count(signum) == 1:
            raise OSError("temporary installation failure")

    monkeypatch.setattr(cleanup.signal, "signal", install)
    cleanup.init()
    assert not cleanup._registered
    cleanup.init()
    cleanup.init()
    assert cleanup._registered
    assert len(registrations) == 1
    assert attempts.count(signal.SIGINT) == 1
    assert attempts.count(signal.SIGTERM) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ctypes callback support")
def test_windows_callback_survives_gc_and_registration_is_idempotent(monkeypatch):
    import ctypes

    callbacks = []

    def register(callback, add):
        assert add is True
        callbacks.append(weakref.ref(callback))
        return 1

    monkeypatch.setattr(cleanup, "_windows_ctrl_handler", None)
    monkeypatch.setattr(cleanup.signal, "signal", lambda *args: None)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(SetConsoleCtrlHandler=register)))
    cleanup._init_windows()
    gc.collect()
    cleanup._init_windows()
    assert len(callbacks) == 1
    assert callbacks[0]() is cleanup._windows_ctrl_handler


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ctypes callback support")
def test_windows_failed_registration_can_retry(monkeypatch, caplog):
    import ctypes

    results = iter([0, 1])
    monkeypatch.setattr(cleanup, "_windows_ctrl_handler", None)
    monkeypatch.setattr(cleanup.signal, "signal", lambda *args: None)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
        SetConsoleCtrlHandler=lambda callback, add: next(results))))
    cleanup._init_windows()
    assert cleanup._windows_ctrl_handler is None
    assert "registration failed" in caplog.text
    cleanup._init_windows()
    assert cleanup._windows_ctrl_handler is not None


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ctypes callback support")
def test_console_input_interrupt_race_does_not_leak_to_next_read(monkeypatch):
    import ctypes
    from core import prompt

    monkeypatch.setattr(cleanup, "_windows_ctrl_handler", None)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
        SetConsoleCtrlHandler=lambda callback, add: 1)))
    cleanup._init_windows()
    handler = cleanup._windows_ctrl_handler
    assert not handler(0)  # Work outside the read retains normal SIGINT handling.
    calls = iter(["x", "y"])

    def getwch():
        value = next(calls)
        if value == "x":
            assert handler(0)  # Ctrl+C arrived as the real key was read.
        return value

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(kbhit=lambda: True, getwch=getwch))
    assert prompt._getwch() == "\x03"
    assert prompt._getwch() == "y"
    assert not handler(0)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows console input")
def test_console_input_state_is_cleared_after_read_failure(monkeypatch):
    import ctypes
    from core import prompt

    monkeypatch.setattr(cleanup, "_windows_ctrl_handler", None)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
        SetConsoleCtrlHandler=lambda callback, add: 1)))
    cleanup._init_windows()
    handler = cleanup._windows_ctrl_handler

    def fail():
        raise OSError("input failed")

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(kbhit=lambda: True, getwch=fail))
    with pytest.raises(OSError, match="input failed"):
        prompt._getwch()
    assert not handler(0)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows initialization path")
def test_init_retries_only_failed_windows_registration(monkeypatch):
    registrations = []
    attempts = []
    monkeypatch.setattr(cleanup, "_registered", False)
    monkeypatch.setattr(cleanup, "_atexit_registered", False)
    monkeypatch.setattr(cleanup, "_registered_signals", set())
    monkeypatch.setattr(cleanup, "_windows_ctrl_handler", None)
    monkeypatch.setattr(cleanup.atexit, "register", lambda fn: registrations.append(fn))
    monkeypatch.setattr(cleanup.signal, "signal", lambda *args: None)

    def register_windows():
        attempts.append(True)
        if len(attempts) == 2:
            cleanup._windows_ctrl_handler = object()

    monkeypatch.setattr(cleanup, "_init_windows", register_windows)
    cleanup.init()
    cleanup.init()
    cleanup.init()
    assert len(registrations) == 1
    assert len(attempts) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows console")
def test_real_ctrl_break_cleans_up_in_hidden_child_console(tmp_path):
    marker = tmp_path / "break.txt"
    script = """
import ctypes, pathlib, sys, time
from core import cleanup
path = pathlib.Path(sys.argv[1])
def callback():
    with path.open('a', encoding='utf-8') as stream:
        stream.write('done\\n')
cleanup.register_cleanup(callback)
cleanup.init()
if cleanup._windows_ctrl_handler is None:
    raise RuntimeError('console handler was not registered')
if not ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, 0):
    raise OSError('GenerateConsoleCtrlEvent failed')
time.sleep(1.5)
raise RuntimeError('Ctrl+Break was not delivered')
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        [sys.executable, "-c", script, str(marker)], cwd=os.getcwd(),
        startupinfo=startup,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        capture_output=True, text=True, timeout=15,
    )
    assert marker.exists(), f"no cleanup: {result.returncode}, {result.stderr}"
    assert marker.read_text(encoding="utf-8") == "done\n"
    assert "Ctrl+Break was not delivered" not in result.stderr


@pytest.mark.parametrize("origin", ["root", "module", "preferences"])
def test_user_exit_cancels_preload_and_applies_update_once(monkeypatch, origin):
    import main
    from core import plugin_entry_preload
    from core.prompt import UserExit

    events = []
    module = SimpleNamespace(icon="", key="example", label="example",
                             entry=lambda: (_ for _ in ()).throw(UserExit()))
    monkeypatch.setattr(main, "discover_modules", lambda cfg: [module])
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending",
                        lambda *, wait=False: events.append(("cancel", wait)))
    monkeypatch.setattr(main, "_on_exit", lambda cfg: events.append(("exit", cfg)))
    if origin == "root":
        monkeypatch.setattr(main, "select", lambda **kwargs: (_ for _ in ()).throw(UserExit()))
    elif origin == "module":
        monkeypatch.setattr(main, "select", lambda **kwargs: "1")
    else:
        monkeypatch.setattr(main, "select", lambda **kwargs: "2")
        monkeypatch.setattr(main, "_handle_preferences", lambda cfg: (_ for _ in ()).throw(UserExit()))
    cfg = {"test": True}
    with pytest.raises(UserExit) as exc:
        main._main_menu(cfg)
    assert exc.value.code == 0
    assert events[-2:] == [("cancel", True), ("exit", cfg)]
    assert events.count(("exit", cfg)) == 1


@pytest.mark.parametrize("value", [None, "cancel"])
def test_root_back_cancels_preload_and_applies_update_once(monkeypatch, value):
    import main
    from core import plugin_entry_preload
    from core.prompt import UserCancel

    events = []
    monkeypatch.setattr(main, "discover_modules", lambda cfg: [])
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending",
                        lambda *, wait=False: events.append(("cancel", wait)))
    monkeypatch.setattr(main, "_on_exit", lambda cfg: events.append(("exit", cfg)))

    def select(**kwargs):
        if value == "cancel":
            raise UserCancel()
        return None

    monkeypatch.setattr(main, "select", select)
    cfg = {"test": True}
    main._main_menu(cfg)
    assert events == [("cancel", True), ("exit", cfg)]

@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows console")
def test_real_ctrl_c_exits_input_and_cleans_up_in_hidden_child_console(tmp_path):
    marker = tmp_path / "ctrl-c.txt"
    script = """
import ctypes, pathlib, sys, threading, time
from core import cleanup
from core.prompt import UserExit, _read_line
kernel = ctypes.windll.kernel32
kernel.CreateFileW.restype = ctypes.c_void_p
handle = kernel.CreateFileW('CONIN$', 0xC0000000, 3, None, 3, 0, None)
if handle in (None, ctypes.c_void_p(-1).value):
    raise OSError('cannot open isolated console input')
if not kernel.SetStdHandle(ctypes.c_ulong(-10 & 0xFFFFFFFF), ctypes.c_void_p(handle)):
    raise OSError('cannot attach isolated console input')
# CREATE_NEW_PROCESS_GROUP starts with Ctrl+C ignored; restore delivery only
# inside this separately created console before registering the handler.
if not kernel.SetConsoleCtrlHandler(None, False):
    raise OSError('cannot enable Ctrl+C delivery')
path = pathlib.Path(sys.argv[1])
def record(value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(value + '\\n')
cleanup.register_cleanup(lambda: record('cleanup'))
cleanup.init()
if cleanup._windows_ctrl_handler is None:
    raise RuntimeError('console handler was not registered')
def send():
    time.sleep(0.5)
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        record('delivery failed')
threading.Thread(target=send, daemon=True).start()
try:
    _read_line()
except UserExit:
    record('exit')
else:
    raise RuntimeError('input returned without UserExit')
from core.windows_console_input import _InputRecord
events = (_InputRecord * 2)()
for event, character in zip(events, ('x', '\\r')):
    event.event_type = 1
    event.event.key.key_down = True
    event.event.key.repeat_count = 1
    event.event.key.character = character
    event.event.key.virtual_key = ord(character.upper())
written = ctypes.c_ulong()
if not kernel.WriteConsoleInputW(ctypes.c_void_p(handle), events, 2, ctypes.byref(written)) or written.value != 2:
    raise OSError('cannot queue next input')
record('next:' + _read_line())
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        [sys.executable, "-c", script, str(marker)], cwd=os.getcwd(),
        stdin=subprocess.DEVNULL, startupinfo=startup,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert marker.read_text(encoding="utf-8") == "exit\nnext:x\ncleanup\n"


@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows console")
@pytest.mark.parametrize("mode", ["work", "redirected", "shield"])
def test_real_ctrl_c_during_work_and_shield_in_hidden_console(tmp_path, mode):
    marker = tmp_path / "work.txt"
    script = """
import ctypes, pathlib, sys, threading, time
from core import cleanup
from core.prompt import shield_ctrlc
kernel = ctypes.windll.kernel32
if not kernel.SetConsoleCtrlHandler(None, False):
    raise OSError('cannot enable Ctrl+C delivery')
if sys.argv[2] != 'redirected':
    kernel.CreateFileW.restype = ctypes.c_void_p
    handle = kernel.CreateFileW('CONIN$', 0xC0000000, 3, None, 3, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError('cannot open isolated console input')
    if not kernel.SetStdHandle(ctypes.c_ulong(-10 & 0xFFFFFFFF), ctypes.c_void_p(handle)):
        raise OSError('cannot attach isolated console input')
path = pathlib.Path(sys.argv[1])
def record(value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(value + '\\n')
cleanup.register_cleanup(lambda: record('cleanup'))
cleanup.init()
def send():
    time.sleep(0.5)
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        record('delivery failed')
threading.Thread(target=send, daemon=True).start()
try:
    if sys.argv[2] == 'shield':
        with shield_ctrlc():
            time.sleep(1.5)
            record('shield finished')
    else:
        time.sleep(1.5)
        record('work finished')
except KeyboardInterrupt:
    record('interrupted')
else:
    raise RuntimeError('Ctrl+C did not interrupt')
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    stdin = subprocess.DEVNULL if mode == "redirected" else None
    result = subprocess.run(
        [sys.executable, "-c", script, str(marker), mode], cwd=os.getcwd(),
        stdin=stdin, startupinfo=startup,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    expected = "shield finished\ninterrupted\ncleanup\n" if mode == "shield" else "interrupted\ncleanup\n"
    assert marker.read_text(encoding="utf-8") == expected


@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows console")
@pytest.mark.parametrize("phase", ["render", "input", "key", "pause"])
def test_root_ctrl_c_during_render_and_input_exits_cleanly(tmp_path, phase):
    marker = tmp_path / "root.txt"
    script = """
import ctypes, pathlib, sys, threading, time
import main
from core import cleanup, plugin_entry_preload, prompt
kernel = ctypes.windll.kernel32
kernel.CreateFileW.restype = ctypes.c_void_p
handle = kernel.CreateFileW('CONIN$', 0xC0000000, 3, None, 3, 0, None)
if handle in (None, ctypes.c_void_p(-1).value):
    raise OSError('cannot open isolated console input')
if not kernel.SetStdHandle(ctypes.c_ulong(-10 & 0xFFFFFFFF), ctypes.c_void_p(handle)):
    raise OSError('cannot attach isolated console input')
if not kernel.SetConsoleCtrlHandler(None, False):
    raise OSError('cannot enable Ctrl+C delivery')
path = pathlib.Path(sys.argv[1])
def record(value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(value + '\\n')
cleanup.register_cleanup(lambda: record('cleanup'))
cleanup.init()
main.discover_modules = lambda cfg: []
original_mode = ctypes.c_ulong()
if not kernel.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(original_mode)):
    raise OSError('cannot get initial console mode')
if not original_mode.value & 1:
    raise RuntimeError('test requires processed input enabled initially')
def on_exit(cfg):
    mode = ctypes.c_ulong()
    if not kernel.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)) or mode.value != original_mode.value:
        raise RuntimeError('console input mode was not restored')
    record('update')
main._on_exit = on_exit
plugin_entry_preload.cancel_pending = lambda **kwargs: record('cancel')
prompt._stdin_is_tty = lambda: True
actual_select = main.select
def send(wait_input):
    if wait_input:
        deadline = time.monotonic() + 5
        while not cleanup._console_input_active and time.monotonic() < deadline:
            time.sleep(.01)
        if not cleanup._console_input_active:
            record('input wait failed')
            return
    else:
        time.sleep(.3)
    if sys.argv[2] in ('key', 'pause'):
        from core.windows_console_input import _InputRecord
        mode = ctypes.c_ulong()
        if not kernel.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)) or mode.value & 1:
            record('Ctrl+C would broadcast outside the program')
            return
        event = _InputRecord()
        event.event_type = 1
        event.event.key.key_down = True
        event.event.key.repeat_count = 1
        event.event.key.virtual_key = 0x43
        event.event.key.character = chr(3)
        event.event.key.control_state = 0x0008  # LEFT_CTRL_PRESSED
        written = ctypes.c_ulong()
        if not kernel.WriteConsoleInputW(ctypes.c_void_p(handle), ctypes.byref(event), 1, ctypes.byref(written)):
            record('key delivery failed')
        return
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        record('delivery failed')
def select(**kwargs):
    if sys.argv[2] == 'render':
        record('render')
        threading.Thread(target=send, args=(False,), daemon=True).start()
        time.sleep(3)
        raise RuntimeError('Ctrl+C was not delivered during rendering')
    def ready():
        record(sys.argv[2])
        threading.Thread(target=send, args=(True,), daemon=True).start()
    if sys.argv[2] == 'pause':
        ready()
        prompt.pause('pause')
        raise RuntimeError('expected UserExit from pause')
    kwargs['on_ready'] = ready
    return actual_select(**kwargs)
main.select = select
main._main_menu({})
raise RuntimeError('expected UserExit')
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        [sys.executable, "-c", script, str(marker), phase], cwd=os.getcwd(),
        stdin=subprocess.DEVNULL, startupinfo=startup,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert marker.read_text(encoding="utf-8") == f"{phase}\ncancel\nupdate\ncleanup\n"


@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows console")
@pytest.mark.parametrize("phase", ["cancel", "update"])
def test_repeated_root_ctrl_c_does_not_repeat_exit_work(tmp_path, phase):
    marker = tmp_path / "repeat.txt"
    script = """
import ctypes, pathlib, sys, threading, time
import main
from core import cleanup, plugin_entry_preload
kernel = ctypes.windll.kernel32
if not kernel.SetConsoleCtrlHandler(None, False):
    raise OSError('cannot enable Ctrl+C delivery')
path = pathlib.Path(sys.argv[1])
def record(value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(value + '\\n')
def send():
    time.sleep(.3)
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        record('delivery failed')
def exit_step(name):
    record(name)
    if name == sys.argv[2]:
        threading.Thread(target=send, daemon=True).start()
        time.sleep(3)
        raise RuntimeError('second Ctrl+C was not delivered')
cleanup.register_cleanup(lambda: record('cleanup'))
cleanup.init()
main.discover_modules = lambda cfg: []
main.select = lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
plugin_entry_preload.cancel_pending = lambda **kwargs: exit_step('cancel')
main._on_exit = lambda cfg: exit_step('update')
main._main_menu({})
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        [sys.executable, "-c", script, str(marker), phase], cwd=os.getcwd(),
        stdin=subprocess.DEVNULL, startupinfo=startup,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode != 0, (result.stdout, result.stderr)
    expected = "cancel\ncleanup\n" if phase == "cancel" else "cancel\nupdate\ncleanup\n"
    assert marker.read_text(encoding="utf-8") == expected
