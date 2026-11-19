"""Native migration contracts and actual Windows profile lifecycle."""
import os

import pytest

from core.native_sandbox import CapabilityRequest, GrantChoice, NativeLimits, NativeSandboxError, get_backend


@pytest.mark.parametrize("platform", ["linux", "darwin", "unknown"])
def test_unimplemented_backend_never_falls_back(platform):
    with pytest.raises(NativeSandboxError):
        get_backend(platform)


def test_three_explicit_grant_choices():
    assert {choice.value for choice in GrantChoice} == {"once", "always", "deny"}


@pytest.mark.parametrize("values", [
    {"cpu_percent": value} for value in (True, 0, 101, 1.5, "25")
] + [{"max_processes": value} for value in (False, 0, 65)]
  + [{"memory_bytes": value} for value in (True, 1, 2 * 1024**3 + 1)])
def test_invalid_native_resource_limits_are_rejected(values):
    with pytest.raises(ValueError, match="native resource limits"):
        NativeLimits(**values)


@pytest.mark.parametrize("field,value", [
    ("plugin", "../other"), ("fingerprint", "latest"),
    ("operation", "shell.execute"), ("resource", "bad\npath"),
])
def test_invalid_capability_is_not_an_authority(field, value):
    args = dict(plugin="game_release", fingerprint="a" * 64,
                operation="tool.execute", resource="creator.build")
    args[field] = value
    with pytest.raises(ValueError):
        CapabilityRequest(**args)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows APIs required")
def test_real_windows_profile_created_and_removed():
    backend = get_backend()
    backend.probe()
    with backend.profile() as (name, sid):
        assert name.startswith("OpsKit.Plugin.")
        assert sid.value


@pytest.mark.skipif(os.name != "nt", reason="actual Windows token isolation required")
def test_real_child_has_appcontainer_token_before_execution():
    executable = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    assert get_backend().run_probe(executable, ["/d", "/c", "exit", "7"]) == 7


@pytest.mark.skipif(os.name != "nt", reason="actual Windows access checks required")
def test_real_child_cannot_read_ungranted_user_file(tmp_path):
    secret = tmp_path / "private.txt"
    secret.write_text("not-for-plugins", encoding="utf-8")
    executable = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    command = f'(set /p value=<{secret}) && (exit /b 21) || (exit /b 22)'
    assert get_backend().run_probe(executable, ["/d", "/s", "/c", command]) == 22


@pytest.mark.skipif(os.name != "nt", reason="actual Windows process cleanup required")
def test_real_probe_timeout_kills_owned_job():
    executable = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    with pytest.raises(TimeoutError):
        get_backend().run_probe(executable, ["/d", "/c", "for /l %i in (1,0,2) do @rem"],
                                timeout=0.1)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows DACL checks required")
def test_staged_read_permission_does_not_allow_write(tmp_path):
    from core.windows_permissions import _descriptor
    staged = tmp_path / "staged"
    staged.mkdir()
    data = staged / "input.txt"
    data.write_text("input", encoding="utf-8")
    original, protected = _descriptor(data)
    before = original.raw
    executable = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    command = f'(set /p value=<{data}) && (exit /b 21) || (exit /b 22)'
    assert get_backend().run_probe(executable, ["/d", "/s", "/c", command], staging=staged) == 21
    command = f'echo forbidden > {data} && (exit /b 21) || (exit /b 22)'
    assert get_backend().run_probe(executable, ["/d", "/s", "/c", command], staging=staged) == 22
    restored, restored_protection = _descriptor(data)
    assert restored.raw == before
    assert restored_protection == protected
    assert data.read_text(encoding="utf-8") == "input"


@pytest.mark.skipif(os.name != "nt", reason="actual Windows DACL checks required")
def test_staged_write_permission_allows_owned_output(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    output = staged / "output.txt"
    executable = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    command = f'echo output > {output} && (exit /b 21) || (exit /b 22)'
    assert get_backend().run_probe(executable, ["/d", "/s", "/c", command],
                                   staging=staged, writable=True) == 21
    assert output.read_text().strip() == "output"


@pytest.fixture
def private_python_runtime(tmp_path):
    import shutil
    import sys
    from pathlib import Path
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = Path(sys.base_prefix)
    for name in ("python.exe", f"python{sys.version_info.major}{sys.version_info.minor}.dll",
                 "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        candidate = source / name
        if candidate.exists():
            shutil.copyfile(candidate, runtime / name)
    # Python 3.10 does not freeze all bootstrap modules used by newer releases.
    shutil.copytree(source / "Lib", runtime / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    return runtime


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Python runtime required")
def test_private_python_runtime_runs_inside_appcontainer(tmp_path, private_python_runtime):
    runtime = private_python_runtime
    import msvcrt
    with open(os.devnull, "rb") as stdin, (tmp_path / "probe.log").open("wb") as output:
        stdio = (msvcrt.get_osfhandle(stdin.fileno()), msvcrt.get_osfhandle(output.fileno()),
                 msvcrt.get_osfhandle(output.fileno()))
        result = get_backend().run_probe(str(runtime / "python.exe"),
            ["-I", "-S", "-c", "raise SystemExit(17)"], staging=runtime, _stdio=stdio)
    assert result == 17, (tmp_path / "probe.log").read_text(errors="replace")


@pytest.mark.skipif(os.name != "nt", reason="actual isolated pipe transport required")
def test_native_worker_bidirectional_pipes_and_eof(private_python_runtime):
    from core.native_process import open_worker
    runtime = private_python_runtime
    code = "import sys; print(input(), flush=True); print(input(), flush=True)"
    with open_worker(get_backend(), str(runtime / "python.exe"), ["-I", "-S", "-u", "-c", code],
                     readonly_trees=(runtime,)) as child:
        child.stdin.write(b"first\n")
        assert child.stdout.readline(4096) == b"first\r\n"
        child.stdin.write(b"second\n")
        assert child.stdout.readline(4096) == b"second\r\n"
        assert child.wait(5) == 0
        assert child.stdout.read(4096) == b""


@pytest.mark.skipif(os.name != "nt", reason="actual isolated runtime snapshot required")
def test_verified_runtime_snapshot_executes_without_host_install_access(private_python_runtime, tmp_path):
    from core.native_runtime import inventory, stage
    from core.native_process import open_worker
    snapshot = tmp_path / "verified-runtime"
    stage(private_python_runtime, snapshot, inventory(private_python_runtime))
    with open_worker(get_backend(), str(snapshot / "python.exe"),
                     ["-I", "-S", "-u", "-c", "print('snapshot', flush=True)"],
                     readonly_trees=(snapshot,)) as child:
        assert child.stdout.readline(4096) == b"snapshot\r\n"
        assert child.wait(5) == 0


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Job cleanup required")
def test_native_context_exit_kills_waiting_worker(private_python_runtime):
    import psutil
    from core.native_process import open_worker
    runtime = private_python_runtime
    with open_worker(get_backend(), str(runtime / "python.exe"),
                     ["-I", "-S", "-u", "-c", "print('ready', flush=True); input()"],
                     readonly_trees=(runtime,)) as child:
        pid = child.pid
        assert child.stdout.readline(4096) == b"ready\r\n"
        assert child.poll() is None
    assert not psutil.pid_exists(pid)


@pytest.mark.skipif(os.name != "nt", reason="actual isolated RPC transport required")
def test_native_protocol_roundtrip_inside_appcontainer(private_python_runtime):
    import time
    from core.native_process import open_worker
    from core.native_protocol import exchange
    runtime = private_python_runtime
    call = 'OPSKIT_RPC:{"rpc_version":1,"type":"call","method":"test","args":[],"kwargs":{}}'
    terminal = 'OPSKIT_RPC:{"rpc_version":1,"type":"result","value":7,"state":""}'
    code = f"print({call!r}, flush=True); reply=input(); print({terminal!r}, flush=True)"
    calls = []
    def dispatch(message):
        calls.append(message)
        return {"value": "accepted"}
    with open_worker(get_backend(), str(runtime / "python.exe"), ["-I", "-S", "-u", "-c", code],
                     readonly_trees=(runtime,)) as child:
        result = exchange(child, deadline=time.monotonic() + 5, dispatch=dispatch,
                          checkpoint=lambda: None, output=lambda _: None)
    assert result["value"] == 7
    assert calls[0]["method"] == "test"


@pytest.mark.skipif(os.name != "nt", reason="actual isolated RPC transport required")
@pytest.mark.parametrize("preload", [False, True])
def test_python_startup_diagnostic_does_not_reach_plugin_output(private_python_runtime, preload):
    import time
    from core.native_process import open_worker
    from core.native_protocol import exchange

    executable = str(private_python_runtime / "python.exe")
    warning = "Failed to find real location of " + executable
    terminal = 'OPSKIT_RPC:{"rpc_version":1,"type":"result","value":7,"state":""}'
    code = (f"import sys; print({warning!r},file=sys.stderr,flush=True); "
            + ("print('OPSKIT_READY:token',flush=True); input(); " if preload else "")
            + f"print('plugin output',flush=True); print({terminal!r},flush=True)")
    output = []
    with open_worker(get_backend(), executable, ["-I", "-X", "utf8", "-S", "-u", "-c", code],
                     readonly_trees=(private_python_runtime,), filter_python_startup=True) as child:
        if preload:
            assert child.stdout.readline(128) == b"OPSKIT_READY:token\r\n"
            child.stdin.write(b"activate\n")
        result = exchange(child, deadline=time.monotonic() + 5, dispatch=lambda _: None,
                          checkpoint=lambda: None, output=output.append)
    assert result["value"] == 7
    assert output == ["plugin output"]


@pytest.mark.skipif(os.name != "nt", reason="actual isolated RPC transport required")
@pytest.mark.parametrize("code", [
    "print('OPSKIT_RPC:bad-json', flush=True)",
    "print('OPSKIT_RPC:{\"rpc_version\":1,\"type\":\"result\",\"value\":0,\"state\":\"\"}', flush=True); "
    "print('OPSKIT_RPC:{\"rpc_version\":1,\"type\":\"call\",\"method\":\"test\",\"args\":[],\"kwargs\":{}}', flush=True)",
])
def test_native_protocol_rejects_invalid_terminal_messages(private_python_runtime, code):
    import time
    from core.native_process import open_worker
    from core.native_protocol import exchange
    runtime = private_python_runtime
    with open_worker(get_backend(), str(runtime / "python.exe"), ["-I", "-S", "-u", "-c", code],
                     readonly_trees=(runtime,)) as child:
        with pytest.raises(NativeSandboxError):
            exchange(child, deadline=time.monotonic() + 5,
                     dispatch=lambda _: pytest.fail("invalid terminal message dispatched"),
                     checkpoint=lambda: None, output=lambda _: None)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Job configuration required")
def test_native_job_has_cpu_memory_and_process_limits_before_resume(private_python_runtime, monkeypatch):
    import ctypes
    from ctypes import wintypes
    from core import windows_job_cpu
    from core.native_process import open_worker
    from core.windows_job import ExtendedLimits
    observed = []
    original = windows_job_cpu.set_cpu_limit
    def apply(job, percent):
        original(job, percent)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.QueryInformationJobObject
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        query.restype = wintypes.BOOL
        cpu = windows_job_cpu.CpuRate()
        limits = ExtendedLimits()
        assert query(job, 15, ctypes.byref(cpu), ctypes.sizeof(cpu), None)
        assert query(job, 9, ctypes.byref(limits), ctypes.sizeof(limits), None)
        observed.append((cpu.flags, cpu.rate, limits.job_memory, limits.basic.active_processes))
    monkeypatch.setattr(windows_job_cpu, "set_cpu_limit", apply)
    runtime = private_python_runtime
    with open_worker(get_backend(), str(runtime / "python.exe"),
                     ["-I", "-S", "-u", "-c", "print('limited', flush=True)"],
                     readonly_trees=(runtime,),
                     limits=NativeLimits(memory_bytes=64 * 1024**2, max_processes=2, cpu_percent=7)) as child:
        assert observed == [(5, 700, 64 * 1024**2, 2)]
        assert child.stdout.readline(4096) == b"limited\r\n"
        assert child.wait(5) == 0


@pytest.mark.skipif(os.name != "nt", reason="actual suspended Windows worker required")
def test_cpu_limit_failure_prevents_any_worker_code(private_python_runtime, monkeypatch):
    from core import windows_job_cpu
    from core.native_process import open_worker
    runtime = private_python_runtime
    marker = runtime / "must-not-run"
    def deny(job, percent):
        raise OSError("injected kernel CPU limit failure")
    monkeypatch.setattr(windows_job_cpu, "set_cpu_limit", deny)
    code = "open(" + repr(str(marker)) + ", 'w').write('ran')"
    with pytest.raises(OSError, match="CPU limit failure"):
        with open_worker(get_backend(), str(runtime / "python.exe"), ["-I", "-S", "-c", code],
                         writable_trees=(runtime,)):
            pytest.fail("unlimited worker resumed")
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="actual native descendant cleanup required")
def test_native_exit_confirms_entire_descendant_tree_is_gone(private_python_runtime):
    import psutil
    from core.native_process import open_worker
    runtime = private_python_runtime
    code = ("import os,sys; os.spawnv(os.P_NOWAIT,sys.executable,"
            "[sys.executable,'-I','-S','-c','import time; time.sleep(60)']); "
            "print('spawned',flush=True); input()")
    with open_worker(get_backend(), str(runtime / "python.exe"), ["-I", "-S", "-u", "-c", code],
                     readonly_trees=(runtime,)) as child:
        assert child.stdout.readline(4096) == b"spawned\r\n"
        descendants = psutil.Process(child.pid).children(recursive=True)
        assert descendants, "fixture must create a live isolated descendant"
        identities = [(process.pid, process.create_time()) for process in descendants]
    for pid, created in identities:
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            continue
        assert process.create_time() != created or process.status() == psutil.STATUS_ZOMBIE


@pytest.mark.skipif(os.name != "nt", reason="actual cleanup failure propagation required")
def test_native_tree_confirmation_failure_is_not_reported_as_success(private_python_runtime, monkeypatch):
    import psutil
    from core import windows_job, windows_job_lifecycle
    from core.native_process import open_worker
    real_terminate = windows_job_lifecycle.terminate_and_wait
    real_close = windows_job.close
    closed = []
    def unconfirmed(job, **kwargs):
        real_terminate(job, **kwargs)
        raise TimeoutError("injected unconfirmed tree cleanup")
    def close(job):
        closed.append(job)
        return real_close(job)
    monkeypatch.setattr(windows_job_lifecycle, "terminate_and_wait", unconfirmed)
    monkeypatch.setattr(windows_job, "close", close)
    runtime = private_python_runtime
    with pytest.raises(TimeoutError, match="unconfirmed tree cleanup"):
        with open_worker(get_backend(), str(runtime / "python.exe"),
                         ["-I", "-S", "-u", "-c", "print('ready',flush=True); input()"],
                         readonly_trees=(runtime,)) as child:
            pid = child.pid
            assert child.stdout.readline(4096) == b"ready\r\n"
    assert closed and not psutil.pid_exists(pid)
