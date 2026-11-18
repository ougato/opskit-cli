"""The old-libc path retains the kernel's atomic non-replacing operation."""
import ctypes
import errno
import os
from pathlib import Path
import platform
import sys
from types import SimpleNamespace

import pytest

from core import directory_publish
from software._shared import archive_install


@pytest.mark.parametrize("machine,number", [("x86_64", 316), ("aarch64", 276)])
@pytest.mark.parametrize("error", [0, errno.EEXIST, errno.ENOSYS, errno.EINVAL, errno.EPERM])
def test_syscall_abi_flags_and_errors(monkeypatch, machine, number, error):
    calls = []

    class Syscall:
        def __call__(self, *args):
            calls.append(args)
            ctypes.set_errno(error)
            return -1 if error else 0

    syscall = Syscall()
    monkeypatch.setattr(directory_publish, "os", SimpleNamespace(name="posix", fsencode=os.fsencode, strerror=os.strerror))
    monkeypatch.setattr(directory_publish.sys, "platform", "linux")
    monkeypatch.setattr(directory_publish.platform, "machine", lambda: machine)
    monkeypatch.setattr(directory_publish.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(syscall=syscall))
    if error:
        with pytest.raises(OSError) as caught:
            archive_install.publish_new_directory(Path("source"), Path("target"))
        assert caught.value.errno == error
    else:
        archive_install.publish_new_directory(Path("source"), Path("target"))
    assert calls == [(number, -100, b"source", -100, b"target", 1)]
    assert syscall.restype is ctypes.c_long
    assert syscall.argtypes == [ctypes.c_long, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]


@pytest.mark.parametrize("machine,pointer_size", [("riscv64", 8), ("i686", 4), ("x86_64", 4)])
def test_unknown_or_non_native_abi_never_calls_syscall(monkeypatch, machine, pointer_size):
    def forbidden(*args):
        pytest.fail("unsupported ABI must never execute a syscall")

    monkeypatch.setattr(directory_publish.platform, "machine", lambda: machine)
    monkeypatch.setattr(directory_publish.ctypes, "sizeof", lambda value: pointer_size)
    with pytest.raises(OSError, match="unavailable"):
        directory_publish._linux_publish(SimpleNamespace(syscall=forbidden), b"source", b"target")


@pytest.mark.parametrize("position", ["source", "destination"])
@pytest.mark.parametrize("utf8_paths", [False, True])
def test_null_path_rejected_before_native_call(monkeypatch, position, utf8_paths):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid path reached native library")

    monkeypatch.setattr(directory_publish, "os", SimpleNamespace(name="posix", fsencode=os.fsencode))
    monkeypatch.setattr(directory_publish.ctypes, "CDLL", forbidden)
    source = Path("source\0suffix" if position == "source" else "source")
    target = Path("target\0suffix" if position == "destination" else "target")
    with pytest.raises(ValueError, match="null bytes"):
        archive_install.publish_new_directory(source, target, utf8_paths=utf8_paths)


@pytest.mark.skipif(not sys.platform.startswith("linux") or platform.machine() not in {"x86_64", "aarch64"}, reason="native supported Linux syscall ABI")
@pytest.mark.parametrize("existing", [None, "empty", "populated"])
def test_native_syscall_publishes_or_preserves_late_target(tmp_path, monkeypatch, existing):
    library = ctypes.CDLL(None, use_errno=True)
    # Hide only the libc wrapper, leaving the actual syscall and errno untouched.
    monkeypatch.setattr(directory_publish.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(syscall=library.syscall))
    destination = tmp_path / "version"
    if existing is None:
        with archive_install.staged_directory(destination, prefix=".owned-") as payload:
            payload.mkdir()
            (payload / "binary").write_bytes(b"owned")
        assert (destination / "binary").read_bytes() == b"owned"
    else:
        with pytest.raises(FileExistsError):
            with archive_install.staged_directory(destination, prefix=".owned-") as payload:
                payload.mkdir()
                (payload / "binary").write_bytes(b"owned")
                destination.mkdir()
                if existing == "populated":
                    (destination / "keep").write_bytes(b"foreign")
        assert not (destination / "binary").exists()
        if existing == "populated":
            assert (destination / "keep").read_bytes() == b"foreign"
    assert not list(tmp_path.glob(".owned-*"))
