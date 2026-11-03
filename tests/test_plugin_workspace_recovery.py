import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core import plugin_workspace as workspace
from core import plugin_workspace_recovery as recovery
from core.resource_lock import ResourceBusyError


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
