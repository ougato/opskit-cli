"""Windows image identity must be based on the mapped file, not its name."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from core.update_windows import current_process_maps_image


@pytest.fixture(autouse=True)
def copied_interpreter_dependencies(tmp_path):
    # A relocated uv interpreter needs its DLLs beside the copied executable.
    for dependency in Path(sys.base_prefix).glob("*.dll"):
        shutil.copy2(dependency, tmp_path / dependency.name)


@pytest.mark.skipif(os.name != "nt", reason="Windows image section query")
def test_image_query_tracks_running_file_not_replacement(tmp_path):
    executable = tmp_path / "running.exe"
    duplicate = tmp_path / "same-bytes.exe"
    alias = tmp_path / "hardlink.exe"
    moved = tmp_path / "renamed.exe"
    shutil.copy2(sys._base_executable, executable)
    shutil.copy2(executable, duplicate)
    os.link(executable, alias)
    project = Path(__file__).resolve().parent.parent
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from core.update_windows import current_process_maps_image
print('ready', flush=True)
for path in sys.stdin:
    path = path.rstrip('\\n')
    if not path:
        break
    print('yes' if current_process_maps_image(path) else 'no', flush=True)
"""
    env = os.environ.copy()
    env["PYTHONHOME"] = sys.base_prefix
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    process = subprocess.Popen([str(executable), "-c", code, str(project)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    try:
        assert process.stdout.readline().strip() == "ready"
        def check(path):
            process.stdin.write(str(path) + "\n")
            process.stdin.flush()
            return process.stdout.readline().strip()
        assert check(executable) == "yes"
        assert check(alias) == "yes"
        assert check(duplicate) == "no"
        # Parent is a different process, despite the child holding this image.
        assert current_process_maps_image(executable) is False
        os.rename(executable, moved)
        # GetModuleFileNameW can still name the old path after a rename.
        # When the positive control cannot identify it, conservatively fall
        # back even though the renamed file is still the running image.
        assert check(moved) == "no"
        shutil.copy2(duplicate, executable)
        assert check(executable) == "no"
        assert check(moved) == "no"
    finally:
        if process.poll() is None:
            process.stdin.write("\n")
            process.stdin.flush()
        process.communicate(timeout=10)
    assert process.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows image section query")
@pytest.mark.parametrize("error", [AttributeError("missing API"), OSError("access denied")])
def test_unavailable_native_query_keeps_existing_behavior(monkeypatch, error):
    import ctypes
    def unavailable(*args, **kwargs):
        raise error
    monkeypatch.setattr(ctypes, "WinDLL", unavailable)
    assert current_process_maps_image(sys.executable) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows image section query")
@pytest.mark.parametrize(("statuses", "expected"), [
    ([0xc0000003], False),                 # Unsupported information class.
    ([0, 0], False),                      # Unexpectedly accepted DLL.
    ([0, 0xc0000008], False),             # Invalid handle is not a non-match.
    ([0, 0xc0000001, 0], True),           # Both controls establish semantics.
    ([0, 0xc0000001, 0xc0000008], False), # Target query failed.
])
def test_native_self_check_requires_exact_control_results(monkeypatch, statuses, expected):
    import ctypes
    from types import SimpleNamespace
    class Query:
        def __init__(self):
            self.statuses = iter(statuses)
        def __call__(self, *args):
            return ctypes.c_long(next(self.statuses)).value
    query = Query()
    original = ctypes.WinDLL
    def library(name, *args, **kwargs):
        return SimpleNamespace(NtQueryInformationProcess=query) if name == "ntdll" else original(name, *args, **kwargs)
    monkeypatch.setattr(ctypes, "WinDLL", library)
    assert current_process_maps_image(sys._base_executable) is expected


@pytest.mark.skipif(os.name != "nt", reason="Windows running executable")
def test_real_running_image_defers_rollback_until_another_process_can_restore(tmp_path, monkeypatch):
    import json
    import sysconfig
    from core import updater, update_target_state
    monkeypatch.setattr(updater, "APP_VERSION", 2)

    target = tmp_path / "application.exe"
    shutil.copy2(sys._base_executable, target)
    data = tmp_path / "data"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    backup = data / "backups" / backup_id
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"verified previous executable")
    old_digest, new_digest = updater._sha256_file(backup), updater._sha256_file(target)
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup_id, "target": str(target.resolve()),
        "from_build": updater.APP_VERSION - 1, "to_build": updater.APP_VERSION,
        "sha256": old_digest, "replacement_sha256": new_digest,
    })
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS, "backup_id": backup_id})
    health_before = health.read_bytes()
    transaction = update_target_state.prepare(target, operation="upgrade",
        from_build=updater.APP_VERSION - 1, to_build=updater.APP_VERSION,
        from_digest=old_digest, to_digest=new_digest, owner_data_dir=data, backup_id=backup_id)
    update_target_state.completed(target, transaction)
    project = Path(__file__).resolve().parent.parent
    code = """
import json, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from core import updater, update_target_state
updater.APP_VERSION = 2
updater._is_frozen = lambda: True
updater._self_path = lambda: Path(sys.executable)
target = updater._self_path()
result = [updater.rollback(), updater.rollback(), updater.rollback()]
print(json.dumps({'result': result, 'transaction': update_target_state.read(target),
                  'stages': len(list(target.parent.glob('.opskit-replace-*')))}))
"""
    env = os.environ.copy()
    env["PYTHONHOME"] = sys.base_prefix
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    process = subprocess.run([str(target), "-c", code, str(project), sysconfig.get_paths()["purelib"]],
                             capture_output=True, text=True, timeout=60, env=env)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["result"] == [False, False, False] and result["stages"] == 0
    assert result["transaction"]["operation"] == "rollback" and result["transaction"]["phase"] == "prepared"
    assert updater._sha256_file(target) == new_digest and updater._sha256_file(backup) == old_digest
    assert health.read_bytes() == health_before
    # After the mapped image has exited, an independent process can restore it.
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    assert updater.rollback() is True
    assert updater._sha256_file(target) == old_digest and updater._sha256_file(backup) == old_digest
    assert health.read_bytes() == health_before
    assert update_target_state.read(target)["phase"] == "applied"
