import os

import pytest

from core import host_file_access as access


def test_real_handle_read_and_bounded_stream(tmp_path):
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"real artifact")
    assert access.read_bounded(target, tmp_path, 100) == b"real artifact"
    with pytest.raises(ValueError, match="bounded"):
        access.read_bounded(target, tmp_path, 3)


def test_hardlinked_file_is_rejected(tmp_path):
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"private")
    os.link(target, tmp_path / "alias.bin")
    with pytest.raises(ValueError, match="ordinary"):
        access.read_bounded(target, tmp_path, 100)


def test_file_changed_while_open_does_not_return_content(tmp_path):
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"before")
    with pytest.raises(ValueError, match="changed during read"):
        with access.open_bounded(target, tmp_path, 100) as stream:
            assert stream.read() == b"before"
            target.write_bytes(b"changed contents")


@pytest.mark.skipif(os.name != "nt", reason="Windows fstat/lstat ctime semantics")
def test_different_handle_and_path_ctime_is_not_a_content_change(tmp_path, monkeypatch):
    from types import SimpleNamespace
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"stable")
    original = access.os.fstat

    def handle_stat(descriptor):
        info = original(descriptor)
        return SimpleNamespace(**{name: getattr(info, name) for name in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_nlink", "st_mode")},
            st_ctime_ns=info.st_ctime_ns + 1000000)

    monkeypatch.setattr(access.os, "fstat", handle_stat)
    assert access.read_bounded(target, tmp_path, 100) == b"stable"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle path verification")
def test_redirected_final_handle_is_rejected_and_closed(tmp_path, monkeypatch):
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"must not read")
    opened = []
    original = access.os.open

    def opening(*args, **kwargs):
        descriptor = original(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(access.os, "open", opening)
    monkeypatch.setattr(access, "_windows_final_path", lambda _: tmp_path / "outside.bin")
    with pytest.raises(ValueError, match="redirected"):
        access.read_bounded(target, tmp_path, 100)
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.skipif(os.name != "nt", reason="Windows junction race")
def test_actual_parent_junction_swap_is_rejected_before_read(tmp_path, monkeypatch):
    import _winapi
    root = tmp_path / "project"
    root.mkdir()
    target = root / "artifact.bin"
    target.write_bytes(b"same inode moved outside authorized directory")
    moved = tmp_path / "outside"
    original = access.os.open
    raced = False

    def opening(path, *args, **kwargs):
        nonlocal raced
        if not raced and path == target:
            raced = True
            root.rename(moved)
            _winapi.CreateJunction(str(moved), str(root))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(access.os, "open", opening)
    try:
        with pytest.raises(ValueError, match="redirected"):
            access.read_bounded(target, root, 100)
        assert raced and (moved / target.name).is_file()
    finally:
        if getattr(root.lstat(), "st_reparse_tag", 0) == 0xA0000003:
            root.rmdir()
