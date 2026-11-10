"""Concurrent writers cannot grant ownership of unrelated files to atomic_write."""
import os
import stat
import subprocess
import sys

import pytest

from core.secrets import atomic_write


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and chmod race")
def test_atomic_write_does_not_chmod_replacement_of_temporary_file(tmp_path, monkeypatch):
    import tempfile

    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"user data")
    unrelated.chmod(0o644)
    real_mkstemp = tempfile.mkstemp

    def replace_temporary(*args, **kwargs):
        descriptor, temporary = real_mkstemp(*args, **kwargs)
        subprocess.run([
            sys.executable, "-c",
            "import os,sys; os.unlink(sys.argv[1]); os.symlink(sys.argv[2], sys.argv[1])",
            temporary, str(unrelated),
        ], check=True, timeout=10)
        return descriptor, temporary

    def stop_publication(*args):
        pytest.fail("replaced staging name must not be published")

    monkeypatch.setattr(tempfile, "mkstemp", replace_temporary)
    monkeypatch.setattr(os, "replace", stop_publication)
    with pytest.raises(ValueError, match="temporary file changed"):
        atomic_write(tmp_path / "config.yaml", "new config")
    assert unrelated.read_bytes() == b"user data"
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o644


def test_atomic_write_preserves_replacement_created_after_commit(tmp_path, monkeypatch):
    original_replace = os.replace
    replacement = []

    def replace_then_external_create(source, target):
        original_replace(source, target)
        subprocess.run([sys.executable, "-c",
                        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'external')",
                        str(source)], check=True, timeout=10)
        replacement.append(source)

    monkeypatch.setattr(os, "replace", replace_then_external_create)
    target = tmp_path / "config.yaml"
    atomic_write(target, "new config")
    assert target.read_text() == "new config"
    assert os.path.isfile(replacement[0])
    with open(replacement[0], "rb") as file:
        assert file.read() == b"external"


def test_atomic_write_preserves_replacement_when_commit_fails(tmp_path, monkeypatch):
    replacement = []

    def replace_before_failure(source, target):
        subprocess.run([sys.executable, "-c",
                        "import os,pathlib,sys; os.unlink(sys.argv[1]); "
                        "pathlib.Path(sys.argv[1]).write_bytes(b'external')",
                        str(source)], check=True, timeout=10)
        replacement.append(source)
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, "replace", replace_before_failure)
    with pytest.raises(OSError, match="synthetic failure"):
        atomic_write(tmp_path / "config.yaml", "new config")
    assert os.path.isfile(replacement[0])
    with open(replacement[0], "rb") as file:
        assert file.read() == b"external"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and rename race")
def test_atomic_write_refuses_temporary_path_replaced_before_commit(tmp_path, monkeypatch):
    import tempfile

    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"user data")
    target = tmp_path / "config.yaml"
    target.write_bytes(b"original config")
    real_mkstemp = tempfile.mkstemp
    paths = []

    def replace_temporary(*args, **kwargs):
        descriptor, temporary = real_mkstemp(*args, **kwargs)
        subprocess.run([sys.executable, "-c",
                        "import os,sys; os.unlink(sys.argv[1]); os.symlink(sys.argv[2], sys.argv[1])",
                        temporary, str(unrelated)], check=True, timeout=10)
        paths.append(temporary)
        return descriptor, temporary

    monkeypatch.setattr(tempfile, "mkstemp", replace_temporary)
    with pytest.raises(ValueError, match="temporary file changed"):
        atomic_write(target, "new config")
    assert target.read_bytes() == b"original config"
    assert unrelated.read_bytes() == b"user data"
    assert os.path.islink(paths[0])


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="a non-cooperating writer can replace the target during commit")
def test_atomic_write_preserves_external_target_created_at_commit(tmp_path, monkeypatch):
    original_replace = os.replace
    target = tmp_path / "config.yaml"
    target.write_bytes(b"original")

    def external_target_then_commit(source, destination):
        subprocess.run([sys.executable, "-c",
                        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'external')",
                        str(destination)], check=True, timeout=10)
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", external_target_then_commit)
    atomic_write(target, "new config")
    assert target.read_bytes() == b"external"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and rename race")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="a non-cooperating writer can replace the stage during commit")
def test_atomic_write_preserves_external_stage_replaced_at_commit(tmp_path, monkeypatch):
    original_replace = os.replace
    target = tmp_path / "config.yaml"
    target.write_bytes(b"original")
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"user data")

    def external_stage_then_commit(source, destination):
        subprocess.run([sys.executable, "-c",
                        "import os,sys; os.unlink(sys.argv[1]); os.symlink(sys.argv[2], sys.argv[1])",
                        str(source), str(unrelated)], check=True, timeout=10)
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", external_stage_then_commit)
    atomic_write(target, "new config")
    assert target.read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL handle verification")
def test_atomic_write_rejects_replaced_windows_temp_without_changing_acl(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    import tempfile

    from core.windows_permissions import _descriptor

    stage = tmp_path / "stage"
    moved = tmp_path / "original-stage"
    target = tmp_path / "config.yaml"
    target.write_bytes(b"original target")
    external_acl = []

    def swapped_mkstemp(*args, **kwargs):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        # Deliberately permit delete-sharing to emulate a replaceable staging
        # handle; stock CPython mkstemp on this host does not permit that.
        handle = create(str(stage), 0xC0000000, 7, None, 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        subprocess.run([sys.executable, "-c",
                        "import os,pathlib,sys; os.rename(sys.argv[1],sys.argv[2]); "
                        "pathlib.Path(sys.argv[1]).write_bytes(b'external')",
                        str(stage), str(moved)], check=True, timeout=10)
        subprocess.run(["icacls", str(stage), "/grant", "*S-1-1-0:R"],
                       check=True, capture_output=True, timeout=10)
        before, protected = _descriptor(stage)
        external_acl.append((before.raw, protected))
        return descriptor, str(stage)

    monkeypatch.setattr(tempfile, "mkstemp", swapped_mkstemp)
    with pytest.raises(ValueError, match="temporary file changed"):
        atomic_write(target, "new config")
    after, protected = _descriptor(stage)
    assert (after.raw, protected) == external_acl[0]
    assert stage.read_bytes() == b"external"
    assert target.read_bytes() == b"original target"
