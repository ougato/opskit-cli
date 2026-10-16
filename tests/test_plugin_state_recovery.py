"""Identity-checked recovery must preserve foreign and committed directories."""
from pathlib import Path
import subprocess
import sys

import pytest

from core import plugin_state as state


def transaction(tmp_path, *, first=False, phase="prepared"):
    parent = tmp_path.resolve()
    root, candidate = parent / "self", parent / ".state-owned/new"
    candidate.mkdir(parents=True)
    (candidate / "value").write_bytes(b"new")
    if not first:
        root.mkdir()
        (root / "value").write_bytes(b"old")
    state.prepare_record(root, candidate, plugin="provider", caller="platform")
    if phase != "prepared":
        if not first:
            root.rename(root.with_name("self.previous"))
        if phase == "published":
            candidate.rename(root)
    return root, candidate


def inspect(root):
    return state.inspect_transaction(root, plugin="provider", caller="platform")


def test_identical_journal_replacement_during_inspection_is_refused(tmp_path, monkeypatch):
    root, candidate = transaction(tmp_path)
    path = state.record_path(root)
    original = path.read_bytes()
    classify = state._inspect_topology

    def replace_journal(target, record):
        plan = classify(target, record)
        replacement = path.with_name("replacement")
        replacement.write_bytes(original)
        replacement.replace(path)
        return plan

    monkeypatch.setattr(state, "_inspect_topology", replace_journal)
    with pytest.raises(state.StateTransactionError, match="record changed"):
        inspect(root)
    assert path.read_bytes() == original
    assert (root / "value").read_bytes() == b"old"
    assert (candidate / "value").read_bytes() == b"new"


def restore(root, **kwargs):
    return state.restore_previous(root, plugin="provider", caller="platform", **kwargs)


@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("phase,expected", [("prepared", "uncommitted"),
                                            ("published", "committed")])
def test_prepared_and_published_topologies_do_not_rename(tmp_path, first, phase, expected):
    root, candidate = transaction(tmp_path, first=first, phase=phase)
    def forbidden(*args):
        pytest.fail("non-recovery topology attempted rename")
    assert inspect(root).phase == expected
    assert restore(root, rename=forbidden).phase == expected
    assert candidate.exists() == (phase == "prepared")
    if phase == "published":
        assert (root / "value").read_bytes() == b"new"
    elif not first:
        assert (root / "value").read_bytes() == b"old"
    else:
        assert not root.exists()
    assert state.record_path(root).exists()


def test_restores_old_state_repeatedly_without_removing_candidate(tmp_path):
    root, candidate = transaction(tmp_path, phase="old_moved")
    assert inspect(root).phase == "restore_previous"
    original = state.record_path(root).read_bytes()
    assert restore(root).phase == "uncommitted"
    assert restore(root).phase == "uncommitted"
    assert (root / "value").read_bytes() == b"old"
    assert (candidate / "value").read_bytes() == b"new"
    assert not root.with_name("self.previous").exists()
    assert state.record_path(root).read_bytes() == original


def test_real_exit_during_restoration_is_repeatable(tmp_path):
    root, candidate = transaction(tmp_path, phase="old_moved")
    script = '''
import os
from pathlib import Path
import sys
from core import plugin_state as state
def rename(source, target):
    os.rename(source, target)
    os._exit(73)
state.restore_previous(Path(sys.argv[1]), plugin="provider", caller="platform", rename=rename)
'''
    result = subprocess.run([sys.executable, "-c", script, str(root)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr
    assert restore(root).phase == "uncommitted"
    assert (root / "value").read_bytes() == b"old"
    assert (candidate / "value").read_bytes() == b"new"


@pytest.mark.parametrize("part", ["root", "backup", "wrapper", "candidate"])
def test_replaced_directories_are_not_claimed(tmp_path, part):
    phase = "prepared" if part == "root" else "old_moved"
    root, candidate = transaction(tmp_path, phase=phase)
    selected = {"root": root, "backup": root.with_name("self.previous"),
                "wrapper": candidate.parent, "candidate": candidate}[part]
    retained = selected.with_name(selected.name + "-retained")
    selected.rename(retained)
    selected.mkdir()
    (selected / "foreign").write_bytes(b"keep")
    original = state.record_path(root).read_bytes()
    with pytest.raises(state.StateTransactionError):
        restore(root)
    assert (selected / "foreign").read_bytes() == b"keep"
    assert retained.is_dir()
    assert state.record_path(root).read_bytes() == original


def test_unknown_wrapper_entry_prevents_old_state_rename(tmp_path):
    root, candidate = transaction(tmp_path, phase="old_moved")
    (candidate.parent / "foreign").write_bytes(b"keep")
    with pytest.raises(state.StateTransactionError, match="unknown entries"):
        restore(root)
    assert not root.exists()
    assert (root.with_name("self.previous") / "value").read_bytes() == b"old"


def test_missing_candidate_before_commit_is_not_guessed(tmp_path):
    root, candidate = transaction(tmp_path, first=True)
    candidate.rename(candidate.with_name("retained"))
    # Move it out of the wrapper; do not trigger the unknown-entry guard instead.
    (candidate.parent / "retained").rename(root.parent / "retained")
    with pytest.raises(state.StateTransactionError, match="ambiguous"):
        restore(root)
    assert not root.exists()
    assert (root.parent / "retained/value").read_bytes() == b"new"


def test_failed_restore_retains_transaction(tmp_path):
    root, candidate = transaction(tmp_path, phase="old_moved")
    def denied(*args):
        raise PermissionError("injected permission failure")
    with pytest.raises(PermissionError):
        restore(root, rename=denied)
    assert inspect(root).phase == "restore_previous"
    assert (candidate / "value").read_bytes() == b"new"


def test_no_record_does_not_restore_legacy_backup(tmp_path):
    root = tmp_path.resolve() / "self"
    backup = root.with_name("self.previous")
    backup.mkdir()
    (backup / "value").write_bytes(b"old")
    with pytest.raises(FileNotFoundError):
        restore(root)
    assert not root.exists()
    assert (backup / "value").read_bytes() == b"old"
