"""Host snapshots and guest state enforce discovery quotas independently."""
from pathlib import Path

import pytest

from core import plugin_sandbox as sandbox, plugin_worker as worker
from core.plugin_tree import bounded_paths


def test_snapshot_directory_flood_refused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(5):
        (source / str(index)).mkdir()
    monkeypatch.setattr(sandbox, "bounded_paths", lambda root, **kw: bounded_paths(root, max_entries=4, **kw))
    with pytest.raises(sandbox.SandboxError, match="tree limits"):
        sandbox._copy_tree(source, tmp_path / "dest", 100)


def test_snapshot_traversal_checks_execution_budget(tmp_path, monkeypatch):
    calls = []
    def expired():
        calls.append(True)
        raise sandbox.SandboxError("execution deadline")
    monkeypatch.setattr(sandbox, "_remaining", expired)
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._copy_tree(tmp_path, tmp_path / "dest", 100)
    assert calls == [True]


def test_worker_state_counts_empty_directories_without_list_materialization(tmp_path, monkeypatch):
    for index in range(4097):
        (tmp_path / str(index)).mkdir()
    monkeypatch.setattr(worker, "Path", lambda value: tmp_path)
    monkeypatch.setattr(Path, "rglob", lambda *args: pytest.fail("unbounded discovery"))
    with pytest.raises(ValueError, match="tree limits"):
        worker._pack_state()


def test_worker_preserves_files_inside_cache_named_state(tmp_path, monkeypatch):
    import base64
    import io
    import tarfile
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "user-data").write_text("state")
    monkeypatch.setattr(worker, "Path", lambda value: tmp_path)
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(worker._pack_state()))) as archive:
        assert archive.extractfile(".git/user-data").read() == b"state"


def test_snapshot_actual_growth_cannot_write_over_budget(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"a")
    calls = []
    def grow():
        if not calls:
            with source.open("ab") as stream:
                stream.write(b"x" * 100)
        calls.append(True)
    monkeypatch.setattr(sandbox, "_remaining", grow)
    with pytest.raises(sandbox.SandboxError, match="exceeds limits"):
        sandbox._copy_snapshot_file(source, target, 64)
    assert not target.exists()


def test_snapshot_replacement_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"trusted")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"untrusted")
    original = sandbox._open_snapshot_source
    def swap(path):
        replacement.replace(source)
        return original(path)
    monkeypatch.setattr(sandbox, "_open_snapshot_source", swap)
    with pytest.raises(sandbox.SandboxError, match="source changed"):
        sandbox._copy_snapshot_file(source, target, 64)
    assert not target.exists()


def test_snapshot_cancel_during_copy_removes_partial_output(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"x" * 131072)
    calls = []
    def cancel():
        calls.append(True)
        if len(calls) == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(sandbox, "_remaining", cancel)
    with pytest.raises(KeyboardInterrupt):
        sandbox._copy_snapshot_file(source, target, 131072)
    assert not target.exists()


def test_snapshot_copy_preserves_contents_and_refuses_existing_target(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"a" * 65537)
    assert sandbox._copy_snapshot_file(source, target, 65537) == 65537
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(FileExistsError):
        sandbox._copy_snapshot_file(source, target, 65537)
    assert target.read_bytes() == source.read_bytes()


def test_snapshot_rejects_hardlinked_source(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"x")
    (tmp_path / "alias").hardlink_to(source)
    with pytest.raises(sandbox.SandboxError, match="ordinary files"):
        sandbox._copy_snapshot_file(source, tmp_path / "target", 64)


@pytest.mark.skipif(sandbox.os.name != "posix", reason="POSIX directory descriptor containment")
def test_snapshot_open_refuses_linked_ancestor(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "file").write_bytes(b"outside")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        sandbox._open_snapshot_source(alias / "file")


def test_state_snapshot_keeps_cache_named_user_data(tmp_path):
    source = tmp_path / "state"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "data").write_bytes(b"user")
    sandbox._copy_tree(source, tmp_path / "seed", 64, skip_dirs=frozenset())
    assert (tmp_path / "seed" / ".git" / "data").read_bytes() == b"user"
