"""An unavailable file identifier must never authorize update mutations."""
import os
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import updater
from core.file_identity import same_file_identity
from core.file_digest import file_digest


class StatWithUnavailableIdentity:
    def __init__(self, original, field, value):
        self.original = original
        self.field = field
        self.value = value

    def __getattr__(self, name):
        return self.value if name == self.field else getattr(self.original, name)


def hide_identity(monkeypatch, field, value):
    fstat = os.fstat
    lstat = Path.lstat
    monkeypatch.setattr(os, "fstat", lambda fd: StatWithUnavailableIdentity(fstat(fd), field, value))
    monkeypatch.setattr(Path, "lstat", lambda path: StatWithUnavailableIdentity(lstat(path), field, value))


def test_zero_device_can_still_identify_distinct_files():
    first = SimpleNamespace(st_dev=0, st_ino=7)
    assert same_file_identity(first, SimpleNamespace(st_dev=0, st_ino=7))
    assert not same_file_identity(first, SimpleNamespace(st_dev=0, st_ino=8))
    assert not same_file_identity(first, SimpleNamespace(st_ino=7))


@pytest.mark.parametrize("field,value", [("st_dev", None), ("st_ino", None), ("st_ino", 0)])
def test_backup_rejects_missing_identity(tmp_path, monkeypatch, field, value):
    source, backup = tmp_path / "source", tmp_path / "backup"
    source.write_bytes(b"original")
    digest = file_digest(source)
    hide_identity(monkeypatch, field, value)
    with pytest.raises(ValueError, match="backup identity"):
        updater._create_update_backup(source, backup, old_digest=digest,
                                      new_digest="a" * 64, new_version=1)
    assert source.read_bytes() == b"original"
    assert backup.read_bytes() == b"original"
    assert not backup.with_name(backup.name + ".json").exists()


def test_backup_receipt_rejects_zero_inode(tmp_path, monkeypatch):
    source, backup = tmp_path / "source", tmp_path / "backup"
    source.write_bytes(b"original")
    digest = file_digest(source)
    receipt = backup.with_name(backup.name + ".json")
    fstat, lstat = os.fstat, Path.lstat

    def patched_fstat(fd):
        result = fstat(fd)
        return StatWithUnavailableIdentity(result, "st_ino", 0) if result.st_size > 8 else result

    def patched_lstat(path):
        result = lstat(path)
        return StatWithUnavailableIdentity(result, "st_ino", 0) if path == receipt else result

    monkeypatch.setattr(os, "fstat", patched_fstat)
    monkeypatch.setattr(Path, "lstat", patched_lstat)
    with pytest.raises(ValueError, match="receipt identity"):
        updater._create_update_backup(source, backup, old_digest=digest,
                                      new_digest="a" * 64, new_version=1)
    assert source.read_bytes() == backup.read_bytes() == b"original"
    assert receipt.is_file()


def hide_inode_for_path(monkeypatch, path):
    stat = Path.stat
    fstat = os.fstat
    original = stat(path)

    def patched_stat(candidate, *args, **kwargs):
        info = stat(candidate, *args, **kwargs)
        return StatWithUnavailableIdentity(info, "st_ino", 0) if candidate == path else info

    def patched_fstat(fd):
        info = fstat(fd)
        if (info.st_dev, info.st_ino) == (original.st_dev, original.st_ino):
            return StatWithUnavailableIdentity(info, "st_ino", 0)
        return info

    monkeypatch.setattr(Path, "stat", patched_stat)
    monkeypatch.setattr(os, "fstat", patched_fstat)


def test_update_record_rejects_zero_inode_and_preserves_bytes(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    record.write_bytes(b'{"key":"value"}')
    hide_inode_for_path(monkeypatch, record)
    with pytest.raises(ValueError, match="record changed"):
        updater._read_update_record(record)
    assert record.read_bytes() == b'{"key":"value"}'


def test_candidate_rejects_zero_inode_and_preserves_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    identity, version = "a" * 32, updater.APP_VERSION + 1
    updater._write_update_record(updater._get_cache_path(), {"candidate_id": identity, "pending_version": version})
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"candidate")
    manifest = pending.parent / "candidate.json"
    updater._write_update_record(manifest, {"schema_version": 1, "candidate_id": identity,
        "build": version, "state": "verified", "sha256": hashlib.sha256(b"candidate").hexdigest()})
    originals = pending.read_bytes(), manifest.read_bytes(), updater._get_cache_path().read_bytes()
    hide_inode_for_path(monkeypatch, pending)
    monkeypatch.setattr(updater, "_do_apply", lambda *args: pytest.fail("unidentified candidate applied"))
    assert updater._apply_owned_pending(pending, version) is False
    assert (pending.read_bytes(), manifest.read_bytes(), updater._get_cache_path().read_bytes()) == originals


@pytest.mark.parametrize("field,value", [("st_dev", None), ("st_ino", None), ("st_ino", 0)])
def test_staging_rejects_missing_identity(tmp_path, monkeypatch, field, value):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"replacement")
    target.write_bytes(b"original")
    hide_identity(monkeypatch, field, value)
    with pytest.raises(RuntimeError, match="staging identity"):
        updater._atomic_copy(source, target)
    assert target.read_bytes() == b"original"
    staged, = tmp_path.glob(".opskit-replace-*")
    assert staged.read_bytes() == b"replacement"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows DACLs")
@pytest.mark.parametrize("field,value", [("st_dev", None), ("st_ino", None), ("st_ino", 0)])
def test_dacl_rejects_missing_identity_without_changing_other_file(tmp_path, monkeypatch, field, value):
    from core.secrets import restrict_permissions
    from core.windows_permissions import _descriptor, copy_dacl_to_verified_file

    source, expected, other = (tmp_path / name for name in ("source", "expected", "other"))
    for path in (source, expected, other):
        path.write_bytes(b"data")
    restrict_permissions(source)
    before, protection = _descriptor(other)
    expected_info = StatWithUnavailableIdentity(expected.stat(), field, value)
    hide_identity(monkeypatch, field, value)
    with pytest.raises(RuntimeError, match="staging identity"):
        copy_dacl_to_verified_file(source, other, expected_info)
    after, after_protection = _descriptor(other)
    assert after.raw == before.raw and after_protection == protection
