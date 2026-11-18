import os
from pathlib import Path
import subprocess
import sys

import pytest

from core import windows_profile_recovery as recovery
from core.native_sandbox import get_backend

pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows AppContainer profile recovery")


def test_normal_profile_lifetime_retires_its_record_and_lease():
    with get_backend().profile() as (name, sid):
        values = recovery.inspect()
        assert len(values) == 1 and values[0]["phase"] == "created"
        assert values[0]["profile"] == name
    assert recovery.inspect() == []
    assert list(recovery._root().iterdir()) == []


def test_active_profile_cannot_be_recovered():
    from core.resource_lock import ResourceBusyError
    with get_backend().profile():
        value = recovery.inspect()[0]
        with pytest.raises(ResourceBusyError):
            recovery.recover(value["transaction"], value["record_sha256"])
        assert recovery.inspect() == [value]


def _crash_profile():
    code = "from core.native_sandbox import get_backend\nimport os\nwith get_backend().profile(): os._exit(71)\n"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
    assert result.returncode == 71, result.stdout + result.stderr
    value = recovery.inspect()[0]
    assert value["valid"] and value["phase"] == "created"
    return value


def _remove_owned_after_failure(value):
    # The test's own exact generated profile only, never user or unknown profiles.
    api, _ = get_backend()._api()
    api.DeleteAppContainerProfile(value["profile"])


def test_real_process_exit_profile_can_be_recovered():
    value = _crash_profile()
    try:
        record, _ = recovery._read(value["transaction"])
        folder = Path(record["folder"])
        assert folder.is_dir()
        assert recovery.recover(value["transaction"], value["record_sha256"]) == {
            "transaction": value["transaction"], "removed": True}
        assert recovery.inspect() == []
        assert not folder.exists()
    finally:
        _remove_owned_after_failure(value)


@pytest.mark.timeout(30)
def test_owner_crash_kills_live_worker_before_profile_recovery():
    """Observe the actual process handle, avoiding PID-reuse assumptions."""
    import ctypes
    from ctypes import wintypes
    import time
    import psutil

    code = (
        "from core.native_sandbox import get_backend\n"
        "import os\n"
        "tool = os.path.join(os.environ['SystemRoot'], 'System32', 'cmd.exe')\n"
        "command = 'start \"\" /b \"' + tool + '\" /d /c \"for /l %i in (1,0,2) do @rem\" & for /l %i in (1,0,2) do @rem'\n"
        "with get_backend().launch(tool, ['/d', '/c', command]) as child:\n"
        "    assert child.poll() is None\n"
        "    print(child.pid, flush=True)\n"
        "    input()\n"
        "    os._exit(72)\n"
    )
    owner = subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles, value = [], None
    try:
        line = owner.stdout.readline()
        assert line.strip().isdecimal(), line
        handle = kernel.OpenProcess(0x100000, False, int(line))  # SYNCHRONIZE only
        assert handle, ctypes.get_last_error()
        handles.append(handle)
        assert kernel.WaitForSingleObject(handle, 0) == 258  # still running
        deadline = time.monotonic() + 5
        descendants = []
        while time.monotonic() < deadline:
            descendants = psutil.Process(int(line)).children(recursive=True)
            if descendants:
                break
            time.sleep(0.02)
        assert descendants, "sandbox worker did not create its descendant"
        for descendant in descendants:
            handle = kernel.OpenProcess(0x100000, False, descendant.pid)
            assert handle, ctypes.get_last_error()
            handles.append(handle)
            assert kernel.WaitForSingleObject(handle, 0) == 258
        value = recovery.inspect()[0]
        assert value["phase"] == "created"
        _, stderr = owner.communicate("\n", timeout=10)
        assert owner.returncode == 72, stderr
        # The owner's last Job handle closes on os._exit; no cleanup finally ran.
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 10000) == 0
        assert recovery.recover(value["transaction"], value["record_sha256"])["removed"]
        assert recovery.inspect() == []
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)
        for handle in handles:
            kernel.CloseHandle(handle)
        if value:
            _remove_owned_after_failure(value)


@pytest.mark.parametrize("change", ["digest", "record", "folder"])
def test_changed_record_or_profile_directory_is_retained(change):
    value = _crash_profile()
    try:
        record, _ = recovery._read(value["transaction"])
        digest = value["record_sha256"]
        if change == "digest":
            digest = "0" * 64
        elif change == "record":
            recovery._path(value["transaction"]).write_text("{}")
        else:
            record["identity"] = [record["identity"][0], record["identity"][1] + 1]
            recovery._write(value["transaction"], record)
            digest = recovery._read(value["transaction"])[1]
        with pytest.raises((OSError, ValueError)):
            recovery.recover(value["transaction"], digest)
        assert Path(record["folder"]).is_dir()
        assert recovery._path(value["transaction"]).exists()
    finally:
        _remove_owned_after_failure(value)


def test_creation_intent_does_not_authorize_profile_removal(monkeypatch):
    root = recovery._root()
    root.mkdir(parents=True)
    token = "a" * 32
    recovery._write(token, {"schema_version": 1, "name": "OpsKit.Plugin." + token,
                            "phase": "creating", "sid": None, "folder": None, "identity": None})
    digest = recovery._read(token)[1]
    monkeypatch.setattr("core.windows_appcontainer.WindowsBackend._api",
                        lambda *args: pytest.fail("unconfirmed profile reached deletion API"))
    with pytest.raises(ValueError, match="unconfirmed"):
        recovery.recover(token, digest)
    assert recovery._path(token).is_file()


@pytest.mark.parametrize("mode", ["normal", "recovery"])
def test_crash_after_confirmed_deletion_retires_only_journal(monkeypatch, mode):
    value = _crash_profile() if mode == "recovery" else None
    code = (
        "from core import windows_profile_recovery as r\n"
        "from core.native_sandbox import get_backend\n"
        "import os, sys\n"
        "original = r._write\n"
        "def write(token, value):\n"
        "    original(token, value)\n"
        "    if value['phase'] == 'removed': os._exit(74)\n"
        "r._write = write\n"
    )
    if mode == "normal":
        code += "with get_backend().profile(): pass\n"
        args = []
    else:
        code += "r.recover(sys.argv[1], sys.argv[2])\n"
        args = [value["transaction"], value["record_sha256"]]
    result = subprocess.run([sys.executable, "-c", code, *args],
                            capture_output=True, text=True, timeout=15)
    try:
        assert result.returncode == 74, result.stdout + result.stderr
        item = recovery.inspect()[0]
        record, _ = recovery._read(item["transaction"])
        assert record["phase"] == "removed"
        assert not Path(record["folder"]).exists()
        monkeypatch.setattr("core.windows_appcontainer.WindowsBackend._api",
                            lambda *args: pytest.fail("completed deletion called Windows API again"))
        recovery.recover(item["transaction"], item["record_sha256"])
        assert recovery.inspect() == []
        assert list(recovery._root().iterdir()) == []
    finally:
        if value and result.returncode != 74:
            _remove_owned_after_failure(value)
