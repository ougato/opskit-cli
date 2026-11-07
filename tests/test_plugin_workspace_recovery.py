import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from core import plugin_workspace as workspace
from core import plugin_workspace_recovery as recovery
from core.resource_lock import ResourceBusyError


def small_capacity_allocation_worker(connection, request, *arguments):
    from core import plugin_workspace_budget as budget
    budget.WORKSPACE_BYTES = 1024
    budget.MAX_WORKSPACE_BYTES = 4096
    workspace._allocate_worker(connection, request, *arguments)


def test_normal_workspace_retires_record_and_locks():
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as owned:
        assert recovery.inspect()[0]["directory"] == str(owned.root)
        (owned.root / "evidence").write_bytes(b"owned")
    assert not owned.root.exists()
    assert list(recovery._root().iterdir()) == []


def test_active_workspace_cannot_be_recovered():
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30):
        item = recovery.inspect()[0]
        with pytest.raises(ResourceBusyError):
            recovery.recover(item["transaction"], item["record_sha256"])


def _fill_unknown_slots():
    from core.paths import data_dir
    from core.plugin_workspace_budget import MAX_WORKSPACES
    parent = data_dir() / "plugin-runtimes" / "workspaces"
    for index in range(MAX_WORKSPACES - 1):
        path = parent / f"unowned-{index}"
        path.mkdir()
        (path / "keep").write_text("preserve")
    return parent


def test_full_namespace_recovers_one_crashed_workspace_and_retries():
    item = _crash()
    abandoned = Path(item["directory"])
    parent = _fill_unknown_slots()
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as current:
        assert current.root.is_dir()
        assert not abandoned.exists()
        assert all((parent / f"unowned-{index}" / "keep").read_text() == "preserve"
                   for index in range(7))
    assert recovery.inspect() == []


def test_full_namespace_skips_record_with_missing_directory(monkeypatch):
    missing = _crash()
    missing_workspace, _ = recovery._read(missing["transaction"])
    workspace.cleanup(missing_workspace, timeout=30)
    _crash()
    abandoned = next(item for item in recovery.inspect() if item["transaction"] != missing["transaction"])
    parent = _fill_unknown_slots()
    inspect = recovery.inspect
    monkeypatch.setattr(recovery, "inspect", lambda: sorted(
        inspect(), key=lambda item: item["transaction"] != missing["transaction"]))
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as current:
        assert current.root.is_dir()
        assert not Path(abandoned["directory"]).exists()
        assert len(list(parent.glob("unowned-*"))) == 7
    assert recovery._path(missing["transaction"]).exists()


def test_byte_capacity_recovers_multiple_idle_workspaces(monkeypatch):
    for _ in range(3):
        _crash()
    records = recovery.inspect()
    parent = Path(records[0]["directory"]).parent
    evidence = parent / "unowned-evidence"
    evidence.write_bytes(b"x" * 1800)
    monkeypatch.setattr(workspace, "_allocate_worker", small_capacity_allocation_worker)
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as current:
        assert current.root.is_dir()
        # Two of the three idle records must be retired to fit the next task.
        assert sum(Path(item["directory"]).exists() for item in records) == 1
        assert evidence.read_bytes() == b"x" * 1800


def test_workspace_allocation_retries_a_temporarily_busy_budget_lock(monkeypatch):
    import threading
    from core.plugin_state_budget import platform_state_guard
    ready, release = threading.Event(), threading.Event()
    def hold_lock():
        with platform_state_guard():
            ready.set()
            release.wait(15)
    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert ready.wait(10)
    original = workspace.allocate
    busy = []
    def allocate(**kwargs):
        try:
            return original(**kwargs)
        except workspace.WorkspaceAllocationBusy:
            busy.append(True)
            release.set()
            raise
    monkeypatch.setattr(workspace, "allocate", allocate)
    try:
        with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as current:
            assert current.root.is_dir()
        assert busy
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()


def test_full_namespace_never_recovers_active_or_unknown_workspaces():
    from core.plugin_sandbox import WorkspaceError
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as active:
        parent = _fill_unknown_slots()
        with pytest.raises(WorkspaceError) as error:
            with workspace.owned_workspace(timeout=30, cleanup_timeout=30):
                pytest.fail("full namespace must not be allocated")
        assert error.value.reason == "capacity"
        assert active.root.is_dir()
        assert len(list(parent.glob("unowned-*"))) == 7


def test_full_namespace_never_recovers_a_replaced_workspace():
    from core.plugin_sandbox import WorkspaceError
    item = _crash()
    original = Path(item["directory"])
    original.rename(original.with_name("retained-original"))
    original.mkdir()
    (original / "keep").write_text("replacement")
    # The original and replacement each occupy a slot; six unrelated
    # directories fill the rest without granting recovery authority.
    for index in range(6):
        (original.parent / f"unowned-{index}").mkdir()
    with pytest.raises(WorkspaceError) as error:
        with workspace.owned_workspace(timeout=30, cleanup_timeout=30):
            pytest.fail("replaced workspace must not be reclaimed")
    assert error.value.reason == "capacity"
    assert (original / "keep").read_text() == "replacement"
    assert (original.parent / "retained-original" / "evidence").read_bytes() == b"owned"


def _crash():
    code = (
        "from core.plugin_workspace import owned_workspace\n"
        "import os\n"
        "if __name__ == '__main__':\n"
        "    with owned_workspace(timeout=30, cleanup_timeout=30) as owned:\n"
        "        (owned.root / 'evidence').write_bytes(b'owned')\n"
        "        os._exit(75)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=40)
    assert result.returncode == 75, result.stdout + result.stderr
    return recovery.inspect()[0]


def test_real_owner_crash_workspace_is_recoverable():
    item = _crash()
    path = Path(item["directory"])
    assert (path / "evidence").read_bytes() == b"owned"
    assert recovery.recover(item["transaction"], item["record_sha256"])["removed"]
    assert not path.exists()
    assert list(recovery._root().iterdir()) == []


@pytest.mark.parametrize("change", ["digest", "record", "replacement", "parent"])
def test_changed_workspace_is_retained(change):
    item = _crash()
    path = Path(item["directory"])
    digest = item["record_sha256"]
    if change == "digest":
        digest = "0" * 64
    elif change == "record":
        recovery._path(item["transaction"]).write_text("{}")
    elif change == "replacement":
        path.rename(path.with_name(path.name + "-original"))
        path.mkdir()
        (path / "evidence").write_bytes(b"replacement")
    else:
        import json
        journal = recovery._path(item["transaction"])
        value = json.loads(journal.read_text())
        value["workspace"]["parent"][1] += 1
        journal.write_text(json.dumps(value))
        digest = hashlib.sha256(journal.read_bytes()).hexdigest()
    with pytest.raises((OSError, ValueError, RuntimeError)):
        recovery.recover(item["transaction"], digest)
    assert (path / "evidence").read_bytes() in {b"owned", b"replacement"}
    assert recovery._path(item["transaction"]).exists()


def test_absent_tree_after_interrupted_cleanup_can_retire_record():
    item = _crash()
    owned, _ = recovery._read(item["transaction"])
    workspace.cleanup(owned, timeout=30)
    recovery.recover(item["transaction"], item["record_sha256"])
    assert list(recovery._root().iterdir()) == []


def test_running_cleanup_worker_prevents_second_cleanup():
    item = _crash()
    with recovery._lock(item["transaction"], worker=True).hold():
        with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
            recovery.recover(item["transaction"], item["record_sha256"])
        assert (Path(item["directory"]) / "evidence").read_bytes() == b"owned"
    recovery.recover(item["transaction"], item["record_sha256"])


def test_unknown_workspace_is_not_adopted():
    owned = workspace.allocate(timeout=30)
    try:
        assert recovery.inspect() == []
        with pytest.raises((OSError, ValueError)):
            recovery.recover(owned.root.name.removeprefix("opskit-sandbox-"), "0" * 64)
        assert owned.root.is_dir()
    finally:
        workspace.cleanup(owned, timeout=30)


def test_unknown_record_does_not_create_recovery_namespace():
    assert not recovery._root().exists()
    with pytest.raises(FileNotFoundError):
        recovery.recover("a" * 32, "0" * 64)
    assert not recovery._root().exists()
