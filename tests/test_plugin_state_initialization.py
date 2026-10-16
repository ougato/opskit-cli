"""Partition creation shares the recovery worker's deadline and identity boundary."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_sandbox as sandbox, plugin_state_io as transaction
from worker_test_markers import publish_marker


def blocked_initialization_worker(connection, *args):
    original = Path.mkdir
    def mkdir(path, *positional, **keywords):
        if path.name == "plugin-data":
            read, write = os.pipe()
            publish_marker(os.environ["OPSKIT_TEST_INITIALIZATION_PID"], str(os.getpid()))
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
        return original(path, *positional, **keywords)
    Path.mkdir = mkdir
    transaction._finish_worker(connection, *args)


@pytest.mark.parametrize("caller,partition", [
    ("platform", "self"), ("consumer", "callers/consumer"), ("platform", "callers/platform"),
])
def test_parent_creation_is_worker_owned(tmp_path, monkeypatch, caller, partition):
    base = tmp_path.resolve()
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(base))
    root = base / "plugin-data" / "owner" / partition
    original = Path.mkdir
    def forbidden(path, *args, **kwargs):
        if path.is_relative_to(base / "plugin-data"):
            pytest.fail("parent created state directory")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    sandbox._recover_state(root, plugin="owner", caller=caller, initialize=True)
    assert root.parent.is_dir()
    assert not root.exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_initialization_reaped_and_retryable(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    base = tmp_path.resolve()
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(base))
    root = base / "plugin-data" / "owner" / "self"
    marker = base / "pid"
    monkeypatch.setenv("OPSKIT_TEST_INITIALIZATION_PID", str(marker))
    with monkeypatch.context() as patch:
        patch.setattr(transaction, "_finish_worker", blocked_initialization_worker)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
        else:
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError):
            sandbox._recover_state(root, plugin="owner", caller="platform", initialize=True)
    monkeypatch.setattr(worker, "time", time)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert not root.parent.exists()
    sandbox._recover_state(root, plugin="owner", caller="platform", initialize=True)
    assert root.parent.is_dir()


@pytest.mark.parametrize("partition", ["other/self", "owner/callers/other", "owner/unrelated"])
def test_unbound_target_refused_without_creation(tmp_path, monkeypatch, partition):
    base = tmp_path.resolve()
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(base))
    root = base / "plugin-data" / partition
    with pytest.raises(sandbox.SandboxError, match="recovery refused"):
        sandbox._recover_state(root, plugin="owner", caller="consumer", initialize=True)
    assert not (base / "plugin-data").exists()


def test_linked_ancestor_refused_before_creating_partition(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(base))
    external = base / "external"
    external.mkdir()
    try:
        (base / "plugin-data").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable")
    with pytest.raises(sandbox.SandboxError, match="recovery refused"):
        sandbox._recover_state(base / "plugin-data" / "owner" / "self",
                               plugin="owner", caller="platform", initialize=True)
    assert list(external.iterdir()) == []
