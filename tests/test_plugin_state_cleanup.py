"""Resolved outcomes survive interrupted, bounded ownership-only cleanup."""
from pathlib import Path
import subprocess
import sys

import pytest

from core import plugin_state as state


def prepare(tmp_path, first, phase):
    root, candidate = tmp_path.resolve() / "self", tmp_path.resolve() / ".state-owned/new"
    candidate.mkdir(parents=True)
    (candidate / "value").write_bytes(b"new")
    (candidate / "other").write_bytes(b"new-other")
    if not first:
        root.mkdir()
        (root / "value").write_bytes(b"old")
        (root / "other").write_bytes(b"old-other")
    state.prepare_record(root, candidate, plugin="provider", caller="platform")
    if phase != "prepared":
        if not first:
            root.rename(root.with_name("self.previous"))
        if phase == "published":
            candidate.rename(root)
    return root, candidate


def finish(root, **kwargs):
    return state.finish_transaction(root, plugin="provider", caller="platform", **kwargs)


@pytest.mark.parametrize("first,phase", [(True, "prepared"), (True, "published"),
                                         (False, "prepared"), (False, "old_moved"),
                                         (False, "published")])
def test_complete_and_repeated_cleanup(tmp_path, first, phase):
    root, candidate = prepare(tmp_path, first, phase)
    expected = "committed" if phase == "published" else "aborted"
    assert finish(root) == expected
    assert finish(root) is None
    assert not state.record_path(root).exists()
    assert not candidate.parent.exists()
    assert not root.with_name("self.previous").exists()
    if first and expected == "aborted":
        assert not root.exists()
    else:
        assert (root / "value").read_bytes() == (b"new" if expected == "committed" else b"old")


@pytest.mark.parametrize("first,phase,boundary", [
    (first, phase, boundary)
    for first, phase in [(True, "prepared"), (True, "published"),
                         (False, "prepared"), (False, "old_moved"), (False, "published")]
    for boundary in ["resolved", "entry_removed", "wrapper_removed"]
    if not (first and phase == "published" and boundary == "entry_removed")
])
def test_real_crash_during_cleanup_can_resume(tmp_path, first, phase, boundary):
    root, candidate = prepare(tmp_path, first, phase)
    script = '''
import os
from pathlib import Path
import sys
from core import plugin_state as state
root, boundary = Path(sys.argv[1]), sys.argv[2]
unlink, rmdir = Path.unlink, Path.rmdir
def crash_unlink(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    if boundary == "entry_removed" and path.name in {"value", "other"}:
        os._exit(73)
def crash_rmdir(path, *args, **kwargs):
    rmdir(path, *args, **kwargs)
    if boundary == "wrapper_removed" and path.name == ".state-owned":
        os._exit(73)
def checkpoint():
    if boundary == "resolved" and state.read_record(root, plugin="provider", caller="platform").resolution != "pending":
        os._exit(73)
Path.unlink, Path.rmdir = crash_unlink, crash_rmdir
state.finish_transaction(root, plugin="provider", caller="platform", checkpoint=checkpoint)
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), boundary],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr
    expected = "committed" if phase == "published" else "aborted"
    assert state.read_record(root, plugin="provider", caller="platform").resolution == expected
    assert finish(root) == expected
    assert finish(root) is None
    assert not candidate.parent.exists()
    if first and expected == "aborted":
        assert not root.exists()
    else:
        assert (root / "value").read_bytes() == (b"new" if expected == "committed" else b"old")


def test_cleanup_error_reports_already_committed(tmp_path, monkeypatch):
    root, _ = prepare(tmp_path, False, "published")
    original = state._remove_owned_tree
    def denied(*args):
        raise PermissionError("injected")
    monkeypatch.setattr(state, "_remove_owned_tree", denied)
    with pytest.raises(state.StateCleanupError) as error:
        finish(root)
    assert error.value.outcome == "committed"
    assert (root / "value").read_bytes() == b"new"
    assert state.read_record(root, plugin="provider", caller="platform").resolution == "committed"
    monkeypatch.setattr(state, "_remove_owned_tree", original)
    assert finish(root) == "committed"


def test_partial_resolution_is_retained_without_cleanup(tmp_path):
    root, candidate = prepare(tmp_path, False, "published")
    path = state.record_path(root)
    with path.open("ab") as stream:
        stream.write(b'{"transaction":')
    raw = path.read_bytes()
    with pytest.raises(state.StateTransactionError):
        finish(root)
    assert path.read_bytes() == raw
    assert (root.with_name("self.previous") / "value").read_bytes() == b"old"
    assert candidate.parent.exists()


def test_foreign_cleanup_link_preserves_entire_old_tree(tmp_path):
    root, _ = prepare(tmp_path, False, "published")
    foreign = root.parent / "foreign"
    foreign.write_bytes(b"keep")
    link = root.with_name("self.previous") / "link"
    try:
        link.symlink_to(foreign)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(state.StateCleanupError) as error:
        finish(root)
    assert error.value.outcome == "committed"
    assert foreign.read_bytes() == b"keep"
    assert (link.parent / "value").read_bytes() == b"old"
    assert state.record_path(root).exists()


def test_resolution_sync_failure_preserves_committed_state_and_backup(tmp_path, monkeypatch):
    root, _ = prepare(tmp_path, False, "published")
    original = state.os.fsync
    def full(*args):
        raise OSError("injected storage failure")
    monkeypatch.setattr(state.os, "fsync", full)
    with pytest.raises(state.StateCleanupError) as error:
        finish(root)
    assert error.value.outcome == "committed"
    assert (root / "value").read_bytes() == b"new"
    assert (root.with_name("self.previous") / "value").read_bytes() == b"old"
    with pytest.raises(state.StateCleanupError):
        finish(root)
    assert (root.with_name("self.previous") / "value").read_bytes() == b"old"
    monkeypatch.setattr(state.os, "fsync", original)
    assert finish(root) == "committed"


def test_cancel_after_resolution_retains_recovery_evidence(tmp_path):
    root, candidate = prepare(tmp_path, True, "prepared")
    def cancel():
        if state.read_record(root, plugin="provider", caller="platform").resolution == "aborted":
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        finish(root, checkpoint=cancel)
    assert (candidate / "value").read_bytes() == b"new"
    assert not root.exists()
    assert finish(root) == "aborted"
    assert not state.record_path(root).exists()
