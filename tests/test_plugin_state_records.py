"""Ownership records must not grant authority over unrelated state paths."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core import plugin_state as state


@pytest.fixture
def directories(tmp_path):
    parent = tmp_path.resolve()
    root = parent / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    staging = parent / ".state-owned" / "new"
    staging.mkdir(parents=True)
    (staging / "value").write_bytes(b"new")
    return root, staging


def prepare(root, staging):
    return state.prepare_record(root, staging, plugin="provider", caller="platform")


def read(root, **kwargs):
    return state.read_record(root, plugin="provider", caller="platform", **kwargs)


@pytest.mark.parametrize("first", [False, True])
def test_roundtrip_records_only_ownership_and_never_publishes(directories, first):
    root, staging = directories
    if first:
        (root / "value").unlink()
        root.rmdir()
    record = prepare(root, staging)
    assert record == read(root)
    assert (record.old_identity is None) == first
    assert root.exists() != first
    assert (staging / "value").read_bytes() == b"new"
    assert not root.with_name("self.previous").exists()


@pytest.mark.parametrize("plugin,caller", [("another", "platform"), ("provider", "another")])
def test_foreign_binding_is_refused(directories, plugin, caller):
    root, staging = directories
    prepare(root, staging)
    with pytest.raises(state.StateTransactionError):
        state.read_record(root, plugin=plugin, caller=caller)
    assert (root / "value").read_bytes() == b"old"


@pytest.mark.parametrize("key,value", [
    ("schema", True), ("transaction", "../other"), ("target", "another"),
    ("parent", "/foreign"), ("parent_identity", [0, 1]),
    ("new_identity", [True, 1]), ("old_identity", [1, 0]),
    ("staging", "../foreign"), ("staging", ".state-a/new"),
    ("staging_identity", [0, 0]), ("extra", "field"),
])
def test_corrupt_or_injected_fields_preserve_evidence(directories, key, value):
    root, staging = directories
    prepare(root, staging)
    path = state.record_path(root)
    record = json.loads(path.read_bytes())
    record[key] = value
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    with pytest.raises(state.StateTransactionError):
        read(root)
    assert path.read_bytes() == raw
    assert (root / "value").read_bytes() == b"old"
    assert (staging / "value").read_bytes() == b"new"


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"x" * 4097,
                                  b'{"schema":1,"schema":1}', b'"' + b'a' * 4090 + b'"'])
def test_partial_duplicate_and_oversize_records_fail_closed(directories, raw):
    root, _ = directories
    state.record_path(root).write_bytes(raw)
    with pytest.raises(state.StateTransactionError):
        read(root)
    assert state.record_path(root).read_bytes() == raw


def test_existing_marker_is_never_overwritten(directories):
    root, staging = directories
    path = state.record_path(root)
    path.write_bytes(b"interrupted-write")
    with pytest.raises(FileExistsError):
        prepare(root, staging)
    assert path.read_bytes() == b"interrupted-write"


def test_legacy_backup_is_not_claimed_or_removed(directories):
    root, staging = directories
    backup = root.with_name("self.previous")
    backup.mkdir()
    with pytest.raises(state.StateTransactionError):
        prepare(root, staging)
    assert backup.is_dir()
    assert not state.record_path(root).exists()


def test_record_cannot_follow_or_overwrite_a_link(directories):
    root, staging = directories
    other = root.parent / "foreign"
    other.write_bytes(b"keep")
    try:
        state.record_path(root).symlink_to(other)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(state.StateTransactionError):
        read(root)
    with pytest.raises(FileExistsError):
        prepare(root, staging)
    assert other.read_bytes() == b"keep"


def test_hardlinked_record_is_refused(directories):
    root, staging = directories
    prepare(root, staging)
    os.link(state.record_path(root), root.parent / "another-record")
    with pytest.raises(state.StateTransactionError):
        read(root)


def test_failed_sync_retains_marker_and_both_states(directories, monkeypatch):
    root, staging = directories
    def full(descriptor):
        raise OSError("injected sync failure")
    monkeypatch.setattr(state.os, "fsync", full)
    with pytest.raises(OSError, match="injected"):
        prepare(root, staging)
    assert state.record_path(root).exists()
    assert (root / "value").read_bytes() == b"old"
    assert (staging / "value").read_bytes() == b"new"


def test_record_rejects_overlap(directories):
    root, staging = directories
    prepare(root, staging)
    path = state.record_path(root)
    record = json.loads(path.read_bytes())
    record["new_identity"] = record["old_identity"]
    path.write_text(json.dumps(record))
    with pytest.raises(state.StateTransactionError, match="overlap"):
        read(root)


def test_foreign_staging_is_refused(directories, tmp_path):
    root, _ = directories
    foreign = tmp_path / "foreign" / ".state-owned" / "new"
    foreign.mkdir(parents=True)
    with pytest.raises(state.StateTransactionError):
        prepare(root, foreign)
    assert not state.record_path(root).exists()


def test_real_process_exit_after_record_sync_preserves_both_states(directories):
    root, staging = directories
    script = '''
import os
from pathlib import Path
import sys
from core import plugin_state as state
sync = state.os.fsync
def crash(descriptor):
    sync(descriptor)
    os._exit(73)
state.os.fsync = crash
state.prepare_record(Path(sys.argv[1]), Path(sys.argv[2]), plugin="provider", caller="platform")
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(staging)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr
    assert read(root).old_identity is not None
    assert (root / "value").read_bytes() == b"old"
    assert (staging / "value").read_bytes() == b"new"


def test_replaced_parent_cannot_inherit_record(directories):
    root, staging = directories
    prepare(root, staging)
    content = state.record_path(root).read_bytes()
    parent = root.parent
    retained = parent.with_name(parent.name + "-retained")
    parent.rename(retained)
    parent.mkdir()
    state.record_path(root).write_bytes(content)
    with pytest.raises(state.StateTransactionError, match="parent replaced"):
        read(root)
    assert state.record_path(root).read_bytes() == content
    assert (retained / "self/value").read_bytes() == b"old"
