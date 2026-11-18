"""Temporary payloads are private before writing and retire on every exit."""
from contextlib import contextmanager
import os
import stat

import pytest

from core import private_file as private


def test_binary_roundtrip_permissions_and_close():
    with private.temporary_private_file() as stream:
        assert stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
        private.verify_private_file(stream.fileno())
        stream.write(b"private\x00\xff\r\n")
        stream.seek(0)
        assert stream.read() == b"private\x00\xff\r\n"
    assert stream.closed


def test_consumer_exception_closes_object():
    with pytest.raises(RuntimeError, match="consumer failed"):
        with private.temporary_private_file() as stream:
            raise RuntimeError("consumer failed")
    assert stream.closed


def test_permission_failure_never_yields_and_closes(monkeypatch):
    opened = []
    original = private._windows_temporary_file if os.name == "nt" else private.tempfile.TemporaryFile

    def tracked():
        stream = original()
        opened.append(stream)
        return stream

    def reject(descriptor):
        raise ValueError("unsafe temporary permissions")

    if os.name == "nt":
        monkeypatch.setattr(private, "_windows_temporary_file", tracked)
    else:
        monkeypatch.setattr(private.tempfile, "TemporaryFile", tracked)
    monkeypatch.setattr(private, "verify_private_file", reject)
    with pytest.raises(ValueError, match="unsafe temporary permissions"):
        with private.temporary_private_file():
            pytest.fail("unverified file was yielded")
    assert len(opened) == 1 and opened[0].closed


def test_capture_and_candidate_use_verified_files(monkeypatch):
    from core import process_binary as binary, plugin_fetch_candidate as candidate
    assert binary.temporary_private_file is private.temporary_private_file
    assert candidate.temporary_private_file is private.temporary_private_file
    # Input verification failure must occur before any client is launched.
    @contextmanager
    def reject():
        raise ValueError("unsafe temporary permissions")
        yield

    def forbidden(*args, **kwargs):
        pytest.fail("client launched without private temporary input")

    monkeypatch.setattr(binary, "temporary_private_file", reject)
    monkeypatch.setattr(binary, "start", forbidden)
    with pytest.raises(ValueError, match="unsafe temporary permissions"):
        with binary.capture(["unused"], b"private request", timeout=10):
            pytest.fail("unverified capture yielded")


@pytest.mark.skipif(os.name != "nt", reason="Windows native ACL/delete-on-close")
def test_windows_file_has_explicit_protected_acl_and_is_deleted(monkeypatch, tmp_path):
    import ctypes
    from ctypes import wintypes

    monkeypatch.setattr(private.tempfile, "gettempdir", lambda: str(tmp_path))
    with private.temporary_private_file() as stream:
        path, = tmp_path.iterdir()
        private.verify_private_file(stream.fileno())
        # Independently query the actual opened object's security descriptor.
        import msvcrt
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_security = advapi.GetSecurityInfo
        pointer = ctypes.c_void_p
        get_security.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                                 pointer, pointer, pointer, pointer, ctypes.POINTER(pointer)]
        get_security.restype = wintypes.DWORD
        control = advapi.GetSecurityDescriptorControl
        control.argtypes = [pointer, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        control.restype = wintypes.BOOL
        free = kernel.LocalFree
        free.argtypes = [pointer]
        free.restype = pointer
        descriptor = pointer()
        assert get_security(msvcrt.get_osfhandle(stream.fileno()), 1, 4, None, None,
                            None, None, ctypes.byref(descriptor)) == 0
        try:
            flags, revision = wintypes.WORD(), wintypes.DWORD()
            assert control(descriptor, ctypes.byref(flags), ctypes.byref(revision))
            assert flags.value & 0x1000  # SE_DACL_PROTECTED, no parent inheritance.
        finally:
            free(descriptor)
        stream.write(b"payload")
        with pytest.raises(PermissionError):
            path.open("rb")
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlinked inode and owner mode")
def test_posix_private_inode_is_unlinked():
    with private.temporary_private_file() as stream:
        info = os.fstat(stream.fileno())
        assert info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0
        assert info.st_nlink == 0
