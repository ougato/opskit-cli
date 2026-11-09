"""Lock acquisition cannot trust an unavailable native file identity."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.resource_lock import ResourceIdentityError, ResourceLock


class CustomLockError(RuntimeError):
    pass


@pytest.mark.parametrize("error_type,expected", [
    (RuntimeError, ResourceIdentityError),
    (CustomLockError, CustomLockError),
])
def test_lock_rejects_unavailable_inode(monkeypatch, tmp_path, error_type, expected):
    import core.resource_lock as resource_lock

    path = tmp_path / "resource.lock"
    original_fstat = resource_lock.os.fstat
    original_lstat = Path.lstat

    def no_inode(info):
        return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                               st_dev=info.st_dev, st_ino=0,
                               st_file_attributes=getattr(info, "st_file_attributes", 0))

    def fake_fstat(fd):
        info = original_fstat(fd)
        if path.exists() and (info.st_dev, info.st_ino) == (
                original_lstat(path).st_dev, original_lstat(path).st_ino):
            return no_inode(info)
        return info

    def fake_lstat(candidate, *args, **kwargs):
        info = original_lstat(candidate, *args, **kwargs)
        return no_inode(info) if candidate == path else info

    monkeypatch.setattr(resource_lock.os, "fstat", fake_fstat)
    monkeypatch.setattr(Path, "lstat", fake_lstat)
    lock = ResourceLock(lambda: path, label="test", error_type=error_type)
    with pytest.raises(expected, match="identity"):
        with lock.hold():
            pass
