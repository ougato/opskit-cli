"""Document replacement races using only files owned by each temporary fixture."""
import os
import stat

import pytest

from core import updater


def test_failed_copy_preserves_file_substituted_at_staging_path(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    saved = tmp_path / "saved-stage"
    source.write_bytes(b"replacement")
    target.write_bytes(b"original")
    staged_path = None
    def substitute_then_fail(path, destination):
        nonlocal staged_path
        staged_path = path
        path.rename(saved)
        path.write_bytes(b"unrelated file")
        raise OSError("simulated publication failure")

    monkeypatch.setattr(updater.os, "replace", substitute_then_fail)
    with pytest.raises(OSError, match="simulated publication failure"):
        updater._atomic_copy(source, target)
    assert saved.read_bytes() == b"replacement"
    assert target.read_bytes() == b"original"
    assert staged_path.read_bytes() == b"unrelated file"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="replacement overwrites a concurrent unrelated target")
def test_copy_preserves_target_substituted_before_publication(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    unrelated = tmp_path / "unrelated"
    source.write_bytes(b"replacement")
    target.write_bytes(b"original")
    unrelated.write_bytes(b"unrelated file")
    original_replace = os.replace

    def substitute_target(staged, destination):
        original_replace(unrelated, target)
        original_replace(staged, destination)

    monkeypatch.setattr(updater.os, "replace", substitute_target)
    updater._atomic_copy(source, target)
    assert target.read_bytes() == b"unrelated file"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and symlink semantics")
def test_copy_does_not_change_unrelated_file_through_staging_symlink(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    unrelated = tmp_path / "unrelated"
    saved = tmp_path / "saved-stage"
    source.write_bytes(b"replacement")
    source.chmod(0o751)
    target.write_bytes(b"original")
    unrelated.write_bytes(b"unrelated file")
    unrelated.chmod(0o600)
    original_copy_xattrs = updater._copy_open_file_xattrs

    def substitute_stage(source_fd, stage_fd):
        staged, = tmp_path.glob(".opskit-replace-*")
        staged.rename(saved)
        staged.symlink_to(unrelated)
        original_copy_xattrs(source_fd, stage_fd)

    monkeypatch.setattr(updater, "_copy_open_file_xattrs", substitute_stage)
    with pytest.raises(RuntimeError, match="staging identity changed"):
        updater._atomic_copy(source, target)
    assert unrelated.read_bytes() == b"unrelated file"
    assert target.read_bytes() == b"original"
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o600
