"""Exercise guarded input on an isolated Windows console or POSIX pseudo-TTY."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


class InputDeadline(RuntimeError):
    pass


def child(case, result, fd, prompt_file):
    sys.path.insert(0, str(ROOT))
    if prompt_file:
        spec = importlib.util.spec_from_file_location("core.prompt", prompt_file)
        prompt = importlib.util.module_from_spec(spec)
        sys.modules["core.prompt"] = prompt
        spec.loader.exec_module(prompt)
    else:
        from core import prompt
    original_stdin = sys.stdin
    stream = open("CONIN$", encoding="utf-8") if os.name == "nt" else io.TextIOWrapper(os.fdopen(os.dup(fd), "rb"), encoding="utf-8")
    sys.stdin = stream
    if not prompt._stdin_is_tty():
        raise RuntimeError("native input is not a terminal")
    if os.name == "nt":
        import ctypes
        import ctypes.wintypes
        import msvcrt
        api = ctypes.windll.kernel32.GetConsoleMode
        api.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]
        api.restype = ctypes.wintypes.BOOL

        def terminal_state():
            mode = ctypes.wintypes.DWORD()
            if not api(msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(mode)):
                raise RuntimeError("console mode unavailable")
            return mode.value
    else:
        import termios

        def terminal_state():
            return termios.tcgetattr(stream.fileno())

    before = terminal_state()
    started = time.monotonic()
    ready = False

    def checkpoint():
        nonlocal ready
        if time.monotonic() - started >= 0.4:
            raise InputDeadline
        if not ready and os.name != "nt" and not terminal_state()[3] & termios.ICANON:
            Path(str(result) + ".ready").write_text("ready", encoding="ascii")
            ready = True

    expired = cancelled = False
    try:
        with prompt.guard_input(checkpoint):
            if case.startswith("line"):
                prompt._read_line(secret=True)
            elif case.startswith("sequence"):
                prompt._read_key_seq()
            else:
                prompt._read_key()
    except InputDeadline:
        expired = True
    except KeyboardInterrupt:
        cancelled = True
    finally:
        after = terminal_state()
        sys.stdin = original_stdin
        stream.close()
    data = {"case": case, "deadline_observed": expired,
            "cancellation_observed": cancelled,
            "elapsed_seconds": time.monotonic() - started,
            "guard_restored": prompt._input_guard.get() is None,
            "terminal_restored": before == after}
    with result.open("x", encoding="utf-8") as target:
        json.dump(data, target)


def verify(output, prompt_file):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = ["key", "sequence", "line"]
    if os.name != "nt":
        cases += ["sequence_partial", "line_partial", "key_cancel",
                  "key_signal", "sequence_signal", "line_signal"]
    results = []
    for case in cases:
        result = output / (case + ".json")
        arguments = [sys.executable, str(Path(__file__).resolve()), "--child", case, "--output", str(result)]
        if prompt_file:
            arguments += ["--prompt-file", str(prompt_file.resolve())]
        options = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        master = slave = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            options.update(startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            import pty
            master, slave = pty.openpty()
            arguments += ["--fd", str(slave)]
            options["pass_fds"] = (slave,)
        process = subprocess.Popen(arguments, **options)
        try:
            if case.endswith(("partial", "cancel", "signal")):
                until = time.monotonic() + 5
                while not Path(str(result) + ".ready").exists():
                    if process.poll() is not None or time.monotonic() >= until:
                        raise RuntimeError("native reader did not enter raw input: " + case)
                    time.sleep(0.005)
                if case.endswith("signal"):
                    process.send_signal(signal.SIGINT)
                else:
                    value = b"\x03" if case.endswith("cancel") else b"\x1b[" if case.startswith("sequence") else b"synthetic-secret"
                    os.write(master, value)
            stdout, stderr = process.communicate(timeout=5)
            if process.returncode or len(stdout) + len(stderr) > 16384 or b"synthetic-secret" in stdout:
                raise RuntimeError("native input verification failed: " + case)
            data = json.loads(result.read_text(encoding="utf-8"))
            outcome = "cancellation_observed" if case.endswith(("cancel", "signal")) else "deadline_observed"
            if not all(data[key] for key in (outcome, "guard_restored", "terminal_restored")):
                raise RuntimeError("input deadline or restoration failed: " + case)
            results.append(data)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)
    source = prompt_file or ROOT / "core/prompt.py"
    report = {"platform": sys.platform, "python": sys.version.split()[0],
              "prompt_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "cases": results, "scope": "isolated console/PTY; deadlines and terminal restoration; POSIX SIGINT to owned readers; no sandbox lifecycle or daemon recovery"}
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--child", choices=["key", "sequence", "line", "sequence_partial", "line_partial", "key_cancel", "key_signal", "sequence_signal", "line_signal"])
    parser.add_argument("--fd", type=int)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.output, args.fd, args.prompt_file)
    else:
        print(json.dumps(verify(args.output, args.prompt_file)))
