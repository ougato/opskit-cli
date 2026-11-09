"""Verify a narrow Windows process-handle primitive in disposable processes.

This is not an implementation or an end-to-end test of update recovery.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows process handles")


def _kernel():
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                       wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _child(handle, ready, result, timeout):
    kernel = _kernel()
    try:
        Path(ready).write_text("ready", encoding="utf-8")
        status = kernel.WaitForSingleObject(int(handle), int(timeout))
        Path(result).write_text(str(status), encoding="utf-8")
    finally:
        kernel.CloseHandle(int(handle))


def _parent(ready, result, stay_alive):
    from ctypes import wintypes
    kernel = _kernel()
    process_handle = kernel.GetCurrentProcess()
    inherited = wintypes.HANDLE()
    if not kernel.DuplicateHandle(process_handle, process_handle, process_handle,
                                  ctypes.byref(inherited), 0x00100000, True, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [inherited.value]}
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "child", str(inherited.value),
             ready, result, "500" if stay_alive else "10000"],
            startupinfo=startup, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        kernel.CloseHandle(inherited)
    deadline = time.monotonic() + 8
    while not Path(ready).exists():
        if child.poll() is not None or time.monotonic() >= deadline:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            raise RuntimeError("owned child did not acknowledge readiness")
        time.sleep(.02)
    if stay_alive and child.wait(timeout=5) != 0:
        raise RuntimeError("owned child wait failed")


@pytest.mark.parametrize("stay_alive,expected", [(False, 0), (True, 258)])
def test_inherited_handle_tracks_exact_parent_lifetime(tmp_path, stay_alive, expected):
    ready, result = tmp_path / "ready", tmp_path / "result"
    parent = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "parent", str(ready), str(result),
         "1" if stay_alive else "0"],
        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert parent.returncode == 0, parent.stderr.decode(errors="replace")
    deadline = time.monotonic() + 12
    while not result.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("owned child did not record its wait result")
        time.sleep(.02)
    assert int(result.read_text(encoding="utf-8")) == expected


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="existing helper cannot detect target changes after its final digest check")
def test_existing_helper_retains_target_changed_after_last_check(tmp_path):
    from core.update_windows import _literal, script
    target, artifact = tmp_path / "target.exe", tmp_path / "artifact.exe"
    result, health, lock = tmp_path / "result.json", tmp_path / "health.json", tmp_path / "update.lock"
    target.write_bytes(b"original fixture")
    artifact.write_bytes(b"replacement fixture")
    digest = lambda value: hashlib.sha256(value).hexdigest()
    body = script(target=target, artifact=artifact, result=result, health=health, lock=lock,
                  old_digest=digest(target.read_bytes()), new_digest=digest(artifact.read_bytes()),
                  version=2, parent_pid=0)
    marker = "$phase = 'commit'"
    if body.count(marker) != 1:
        raise RuntimeError("helper commit marker changed")
    ready, release = tmp_path / "commit-ready", tmp_path / "commit-release"
    body = body.replace(marker, f"[IO.File]::WriteAllText({_literal(ready)}, 'ready')\n"
        f"    while (-not [IO.File]::Exists({_literal(release)})) {{ Deadline; Start-Sleep -Milliseconds 10 }}\n"
        "    " + marker)
    helper = tmp_path / "helper.ps1"
    helper.write_bytes(body.encode("utf-8-sig"))
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    process = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    changed = b"concurrent unrelated bytes"
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("isolated helper did not reach the final commit gate")
            time.sleep(.02)
        if target.read_bytes() != b"original fixture":
            raise RuntimeError("isolated target changed before the controlled race")
        # An independent process changes the target while the helper is paused
        # after its final digest check and before its replacement call.
        target.write_bytes(changed)
        release.write_text("release", encoding="utf-8")
        process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
    if not result.exists():
        raise RuntimeError(f"isolated helper failed before recording status: {process.returncode}")
    status = json.loads(result.read_text(encoding="utf-8"))
    if target.read_bytes() != changed and (process.returncode != 0 or status.get("state") != "applied"):
        raise RuntimeError("isolated helper did not complete the expected replacement")
    assert target.read_bytes() == changed


if __name__ == "__main__":
    if sys.argv[1] == "parent":
        _parent(sys.argv[2], sys.argv[3], sys.argv[4] == "1")
    elif sys.argv[1] == "child":
        _child(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
