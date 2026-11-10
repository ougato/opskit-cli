"""Windows 8.3 paths are an optional, pinned execution optimization."""
import os
from pathlib import Path

import pytest

from core import native_short_path
from core.native_runtime import absolute_path


@pytest.mark.skipif(os.name != "nt", reason="Windows alias and sharing semantics")
def test_alias_pins_ancestry_and_executable(tmp_path):
    root = absolute_path(tmp_path)
    while len(str(root)) < 205:
        root /= "long-component-for-short-name"
        root.mkdir()
    exe = root / "runtime" / "python" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")
    with native_short_path.short_execution_root(root, "runtime/python/python.exe") as short:
        if short == root:
            pytest.skip("this volume has no usable 8.3 alias")
        assert os.path.samefile(root, short)
        assert os.path.samefile(exe, short / "runtime/python/python.exe")
        for path in (root, root.parent, exe.parent, exe):
            with pytest.raises(PermissionError):
                path.rename(path.with_name(path.name + "-renamed"))
    # Pins are released before the owned tree's normal cleanup.
    exe.rename(exe.with_name("renamed.exe"))


@pytest.mark.skipif(os.name != "nt", reason="Windows alias and sharing semantics")
def test_alias_identity_mismatch_refuses_execution(tmp_path, monkeypatch):
    root = absolute_path(tmp_path)
    while len(str(root)) < 205:
        root /= "long-component-for-short-name"
        root.mkdir()
    exe = root / "runtime" / "python" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"trusted")
    other = root.parent / "other"
    (other / "runtime" / "python").mkdir(parents=True)
    (other / "runtime" / "python" / "python.exe").write_bytes(b"untrusted")
    monkeypatch.setattr(native_short_path, "_short_path", lambda _: other)
    # Same number of ancestors allows detection at the divergent component.
    with pytest.raises(ValueError, match="identity mismatch"):
        with native_short_path.short_execution_root(root, "runtime/python/python.exe"):
            pytest.fail("mismatched alias was accepted")


@pytest.mark.skipif(os.name != "nt", reason="Windows alias and sharing semantics")
def test_unavailable_alias_retains_original_path(tmp_path, monkeypatch):
    root = absolute_path(tmp_path)
    while len(str(root)) < 205:
        root /= "long-component-for-short-name"
        root.mkdir()
    monkeypatch.setattr(native_short_path, "_short_path", lambda _: root)
    with native_short_path.short_execution_root(root, "runtime/python/python.exe") as result:
        assert result == root
    def unavailable(_):
        raise OSError("no alias")
    monkeypatch.setattr(native_short_path, "_short_path", unavailable)
    with native_short_path.short_execution_root(root, "runtime/python/python.exe") as result:
        assert result == root

@pytest.mark.parametrize("volume,index_high,index_low", [(0, 1, 1), (1, 0, 0)])
def test_missing_windows_file_identity_is_rejected(volume, index_high, index_low):
    from types import SimpleNamespace
    info = SimpleNamespace(volume=volume, index_high=index_high, index_low=index_low,
                           attributes=0x10, links=1)
    with pytest.raises(ValueError, match="invalid file identity"):
        native_short_path._checked_identity(info, directory=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize("relative", ["C:escape.exe", "C:\\escape.exe", "\\escape.exe", "..\\escape.exe"])
def test_rejects_executable_paths_outside_execution_root(tmp_path, relative):
    with pytest.raises(ValueError, match="invalid executable relative path"):
        with native_short_path.short_execution_root(absolute_path(tmp_path), relative):
            pytest.fail("invalid executable path was accepted")

@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS short-name mutation required")
def test_pinned_directory_blocks_short_name_replacement(tmp_path):
    import ctypes
    from ctypes import wintypes
    root = absolute_path(tmp_path)
    while len(str(root)) < 205:
        root /= "long-component-for-short-name"
        root.mkdir()
    exe = root / "runtime" / "python" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")
    control = root.parent / "alias-mutation-control"
    control.mkdir()

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileShortNameW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]
    kernel.SetFileShortNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = ctypes.c_void_p(-1).value
    def change_alias(path, name):
        handle = kernel.CreateFileW(str(path), 0x10000 | 0x100, 7, None, 3, 0x02000000, None)
        if handle == invalid:
            return False, ctypes.get_last_error()
        try:
            ok = kernel.SetFileShortNameW(handle, name)
            return bool(ok), 0 if ok else ctypes.get_last_error()
        finally:
            kernel.CloseHandle(handle)

    ok, error = change_alias(control, "PINCTRL1")
    if not ok and error in {1, 5, 50, 87, 1314}:
        pytest.skip(f"short-name mutation unavailable (Windows error {error})")
    assert ok, f"could not establish mutation control: {error}"
    with native_short_path.short_execution_root(root, "runtime/python/python.exe") as short:
        if short == root:
            pytest.skip("this volume has no usable 8.3 alias")
        ok, error = change_alias(root, "PINTEST1")
        assert not ok and error == 32
        assert os.path.samefile(root, short)

@pytest.mark.skipif(os.name != "nt", reason="Windows alias and reparse semantics")
def test_short_alias_reparse_point_is_rejected(tmp_path, monkeypatch):
    import subprocess
    root = absolute_path(tmp_path)
    while len(str(root)) < 205:
        root /= "long-component-for-short-name"
        root.mkdir()
    exe = root / "runtime" / "python" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"trusted")
    alias = root.parent / "alias"
    made = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(root)],
                          capture_output=True, check=False)
    if made.returncode:
        pytest.skip("Windows junction creation is unavailable")
    assert alias.is_junction()
    monkeypatch.setattr(native_short_path, "_short_path", lambda _: alias)
    with pytest.raises(ValueError, match="reparse point"):
        with native_short_path.short_execution_root(root, "runtime/python/python.exe"):
            pytest.fail("reparse alias was accepted")


@pytest.mark.skipif(os.name != "nt", reason="Windows file identity semantics")
def test_hardlinked_executable_is_rejected(tmp_path):
    root = absolute_path(tmp_path)
    while len(str(root)) < 205:
        root /= "long-component-for-short-name"
        root.mkdir()
    exe = root / "runtime" / "python" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"trusted")
    os.link(exe, exe.with_name("another.exe"))
    if len(str(native_short_path._short_path(root))) >= len(str(root)):
        pytest.skip("this volume has no usable 8.3 alias")
    with pytest.raises(ValueError, match="invalid file identity or type"):
        with native_short_path.short_execution_root(root, "runtime/python/python.exe"):
            pytest.fail("hardlinked executable was accepted")
