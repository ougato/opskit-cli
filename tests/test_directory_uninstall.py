"""Check managed uninstall against an isolated launcher and runtime tree."""

from pathlib import Path
import sys
import tempfile
import time

import pytest

from core import self_uninstall, update_target_state, updater
from core.directory_install import Generation, Install, InstallState
from core.file_digest import file_digest
from core.resource_lock import ResourceLock


@pytest.fixture
def managed_install(tmp_path, monkeypatch):
    if sys.platform == "win32":
        directory = tmp_path / "localappdata" / "opskit"
        monkeypatch.setattr(self_uninstall, "_windows_install_dir", lambda: directory)
        entry = directory / "opskit.exe"
    elif sys.platform.startswith("linux"):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        entry = tmp_path / ".local/bin/opskit"
    else:
        pytest.skip("Managed installations are supported on Windows and Linux")
    entry.parent.mkdir(parents=True)
    entry.write_bytes(b"stable launcher")
    install = Install(entry)
    generation = Generation("g-" + "1" * 32, 1, "1" * 64)
    runtime = install.root / generation.name
    runtime.mkdir(parents=True)
    (runtime / entry.name).write_bytes(b"running generation")
    InstallState(generation).write(install.state_path)
    monkeypatch.setattr(self_uninstall.paths, "_is_frozen", lambda: True)
    monkeypatch.setattr(self_uninstall, "managed_install", lambda: install)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return install, runtime


def _wait_until(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("Timed out waiting for the uninstall worker")


def test_managed_uninstall_removes_entry_and_preserves_runtime(managed_install):
    install, runtime = managed_install
    plan = self_uninstall.plan("program")
    assert plan.executable == install.entry
    assert plan.retained_runtime == install.root
    failure = self_uninstall.execute(plan)
    if failure:
        _wait_until(lambda: not install.entry.exists() or failure.exists())
        assert not failure.exists(), lambda: failure.read_text()
    assert not install.entry.exists()
    assert (runtime / install.entry.name).read_bytes() == b"running generation"
    assert install.state_path.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred uninstall worker")
def test_windows_worker_rejects_an_install_changed_while_waiting(managed_install):
    from core.self_uninstall_windows import schedule

    install, _ = managed_install
    plan = self_uninstall.plan("program")
    state_path = update_target_state.path(install.entry)
    target_lock = ResourceLock(lambda: updater._update_target_lock_path(install.entry), label="test target")
    with install.lock.hold(), target_lock.hold():
        failure = schedule(plan, file_digest(install.entry), state_path, managed=install)
        InstallState(Generation("g-" + "2" * 32, 2, "2" * 64)).write(install.state_path)
    _wait_until(failure.exists)
    assert "OpsKit directory installation changed" in failure.read_text()
    assert install.entry.read_bytes() == b"stable launcher"
