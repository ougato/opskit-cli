"""Owned staging cleanup and native non-replacing directory publication."""
import ctypes
import errno
import os
from types import SimpleNamespace

import pytest

from software._shared import archive_install
from software._shared.archive_install import staged_directory, publish_new_directory


def test_success_publishes_complete_directory(tmp_path):
    dest = tmp_path / "version"
    with staged_directory(dest, prefix=".owned-") as payload:
        payload.mkdir()
        (payload / "binary").write_bytes(b"inert")
        assert not dest.exists()
    assert (dest / "binary").read_bytes() == b"inert"
    assert not list(tmp_path.glob(".owned-*"))


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_exception_or_cancel_cleans_only_own_directory(tmp_path, failure):
    foreign = tmp_path / ".owned-foreign"
    foreign.mkdir()
    (foreign / "keep").write_bytes(b"keep")
    dest = tmp_path / "version"
    with pytest.raises(failure):
        with staged_directory(dest, prefix=".owned-") as payload:
            payload.mkdir()
            (payload / "partial").write_bytes(b"partial")
            raise failure("injected")
    assert not dest.exists()
    assert list(tmp_path.glob(".owned-*")) == [foreign]
    assert (foreign / "keep").read_bytes() == b"keep"


def test_existing_target_rejected(tmp_path):
    dest = tmp_path / "version"
    dest.mkdir()
    (dest / "data").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        with staged_directory(dest, prefix=".owned-"):
            pytest.fail("must reject before staging")
    assert (dest / "data").read_bytes() == b"keep"


@pytest.mark.parametrize("populated", [False, True])
def test_late_destination_race_is_not_replaced(tmp_path, populated):
    dest = tmp_path / "version"
    with pytest.raises(FileExistsError):
        with staged_directory(dest, prefix=".owned-") as payload:
            payload.mkdir()
            (payload / "binary").write_bytes(b"inert")
            dest.mkdir()
            if populated:
                (dest / "data").write_bytes(b"keep")
    assert dest.is_dir() and not (dest / "binary").exists()
    assert not list(tmp_path.glob(".owned-*"))
    if populated:
        assert (dest / "data").read_bytes() == b"keep"


@pytest.mark.parametrize("platform,symbol,expected", [
    ("linux", "renameat2", (-100, b"source", -100, b"target", 1)),
    ("darwin", "renamex_np", (b"source", b"target", 4)),
])
def test_posix_exclusive_flags_and_error_propagation(monkeypatch, platform, symbol, expected):
    calls = []

    class Rename:
        def __call__(self, *args):
            calls.append(args)
            ctypes.set_errno(errno.EEXIST)
            return -1

    monkeypatch.setattr(archive_install, "os", SimpleNamespace(name="posix", fsencode=os.fsencode, strerror=os.strerror))
    monkeypatch.setattr(archive_install.sys, "platform", platform)
    monkeypatch.setattr(archive_install.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(**{symbol: Rename()}))
    from pathlib import Path
    with pytest.raises(FileExistsError):
        publish_new_directory(Path("source"), Path("target"))
    assert calls == [expected]


def test_missing_posix_primitive_has_no_replacing_fallback(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(archive_install, "os", SimpleNamespace(name="posix", fsencode=os.fsencode))
    monkeypatch.setattr(archive_install.sys, "platform", "linux")
    monkeypatch.setattr(archive_install.ctypes, "CDLL", lambda *a, **k: SimpleNamespace())
    with pytest.raises(OSError, match="unavailable"):
        publish_new_directory(Path("source"), Path("target"))


@pytest.mark.parametrize("existing", [False, True])
def test_windows_access_denied_classification_preserves_real_permission_error(tmp_path, monkeypatch, existing):
    from pathlib import Path
    source, destination = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "binary").write_bytes(b"owned")
    if existing:
        destination.mkdir()
        (destination / "data").write_bytes(b"foreign")
    failure = PermissionError("injected Windows access denied")

    def denied(*args, **kwargs):
        raise failure

    monkeypatch.setattr(archive_install, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(Path, "rename", denied)
    with pytest.raises(FileExistsError if existing else PermissionError) as caught:
        publish_new_directory(source, destination)
    if existing:
        assert "refusing to overwrite" in str(caught.value)
        assert caught.value.__cause__ is failure
        assert (destination / "data").read_bytes() == b"foreign"
    else:
        assert caught.value is failure
        assert not destination.exists()
    assert (source / "binary").read_bytes() == b"owned"


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handle sharing")
def test_native_windows_non_delete_shared_file_blocks_then_releases_publication(tmp_path):
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    create = api.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = api.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "binary").write_bytes(b"inert")
    # Share read/write but deliberately deny delete/rename of the child file.
    handle = create(str(source / "binary"), 0x80000000, 3, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(PermissionError) as caught:
            publish_new_directory(source, destination)
        assert caught.value.winerror in (5, 32)
        assert not destination.exists()
        assert (source / "binary").read_bytes() == b"inert"
    finally:
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    publish_new_directory(source, destination)
    assert (destination / "binary").read_bytes() == b"inert"
    assert not source.exists()


@pytest.mark.parametrize("scenario", ["recover", "persistent", "competition", "cancel"])
def test_windows_publication_retry_is_bounded_and_never_replaces(tmp_path, monkeypatch, scenario):
    from pathlib import Path
    source, destination = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "binary").write_bytes(b"owned")
    native_rename = Path.rename
    attempts, waits = [], []
    failure = PermissionError("transient native sharing denial")
    failure.winerror = 5

    def rename(path, target):
        attempts.append(1)
        if scenario == "recover" and len(attempts) == 2:
            return native_rename(path, target)
        raise failure

    def wait(seconds):
        waits.append(seconds)
        if scenario == "competition":
            destination.mkdir()
            (destination / "foreign").write_bytes(b"keep")
        if scenario == "cancel":
            raise KeyboardInterrupt

    monkeypatch.setattr(archive_install, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(archive_install.time, "sleep", wait)
    if scenario == "recover":
        publish_new_directory(source, destination)
        assert (destination / "binary").read_bytes() == b"owned"
        assert len(attempts) == 2 and waits == [0.05]
    else:
        expected = {"persistent": PermissionError, "competition": FileExistsError,
                    "cancel": KeyboardInterrupt}[scenario]
        with pytest.raises(expected):
            publish_new_directory(source, destination)
        assert (source / "binary").read_bytes() == b"owned"
        if scenario == "persistent":
            assert len(attempts) == 5 and sum(waits) == pytest.approx(0.5)
        elif scenario == "competition":
            assert len(attempts) == 2 and (destination / "foreign").read_bytes() == b"keep"
            assert not (destination / "binary").exists()
        else:
            assert len(attempts) == 1
