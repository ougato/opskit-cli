"""Isolated Windows TxF experiments; these do not implement update recovery.

TxF is deprecated and these checks cannot establish crash or power-loss safety
on other filesystems, machines, or security products.
"""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows TxF")
_INVALID = ctypes.c_void_p(-1).value
_UNSUPPORTED = {1, 50, 6805}  # invalid function, not supported, unsupported remote volume


def _api():
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ktm = ctypes.WinDLL("ktmw32", use_last_error=True)
    ktm.CreateTransaction.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPCWSTR]
    ktm.CreateTransaction.restype = wintypes.HANDLE
    ktm.CommitTransaction.argtypes = [wintypes.HANDLE]
    ktm.CommitTransaction.restype = wintypes.BOOL
    kernel.CreateFileTransactedW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                             wintypes.HANDLE, wintypes.HANDLE,
                                             ctypes.c_void_p, ctypes.c_void_p]
    kernel.CreateFileTransactedW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.MoveFileTransactedW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                           ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, wintypes.HANDLE]
    kernel.MoveFileTransactedW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel, ktm


def _transaction(ktm):
    handle = ktm.CreateTransaction(None, None, 0, 0, 0, 0, None)
    if not handle or handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _open(kernel, transaction, path):
    return kernel.CreateFileTransactedW(str(path), 0xC0000000, 7, None, 3, 0,
                                         None, transaction, None, None)


def _require_volume_support(tmp_path):
    """Do not assert that deprecated TxF exists on every Windows volume."""
    probe = tmp_path / "txf-support-probe"
    probe.write_bytes(b"probe")
    kernel, ktm = _api()
    transaction = _transaction(ktm)
    try:
        handle = _open(kernel, transaction, probe)
        if handle == _INVALID:
            error = ctypes.get_last_error()
            if error in _UNSUPPORTED:
                pytest.skip(f"volume does not support TxF (Win32 {error})")
            raise ctypes.WinError(error)
        kernel.CloseHandle(handle)
    finally:
        kernel.CloseHandle(transaction)


def _move(kernel, transaction, source, target):
    if not kernel.MoveFileTransactedW(str(source), str(target), None, None, 1, transaction):
        raise ctypes.WinError(ctypes.get_last_error())


def _abrupt_child(target, stage, commit):
    kernel, ktm = _api()
    transaction = _transaction(ktm)
    target_handle = _open(kernel, transaction, target)
    if target_handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.CloseHandle(target_handle)
    _move(kernel, transaction, stage, target)
    if commit and not ktm.CommitTransaction(transaction):
        raise ctypes.WinError(ctypes.get_last_error())
    # Simulate loss of the helper, without Python cleanup or closing handles.
    os._exit(0)


def test_open_transaction_prevents_external_write_and_parent_rename(tmp_path):
    _require_volume_support(tmp_path)
    directory = tmp_path / "directory"
    directory.mkdir()
    target, stage = directory / "target", directory / "stage"
    target.write_bytes(b"old")
    stage.write_bytes(b"new")
    kernel, ktm = _api()
    transaction = _transaction(ktm)
    try:
        target_handle = _open(kernel, transaction, target)
        if target_handle == _INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(target_handle)
        stage_handle = _open(kernel, transaction, stage)
        if stage_handle == _INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(stage_handle)
        with pytest.raises(OSError):
            target.write_bytes(b"external")
        with pytest.raises(OSError):
            stage.write_bytes(b"external")
        with pytest.raises(OSError):
            stage.rename(directory / "moved-stage")
        with pytest.raises(OSError):
            directory.rename(tmp_path / "moved")
        outsider = subprocess.run([sys.executable, "-c", "\n".join([
            "import json, pathlib, sys",
            "d = pathlib.Path(sys.argv[1])",
            "out = []",
            "for action in (lambda: (d / 'target').write_bytes(b'external'),",
            "               lambda: (d / 'stage').write_bytes(b'external'),",
            "               lambda: d.rename(d.parent / 'moved-external')):",
            "    try: action(); out.append('succeeded')",
            "    except OSError: out.append('blocked')",
            "print(json.dumps(out))",
        ]), str(directory)], capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        assert json.loads(outsider.stdout) == ["blocked", "blocked", "blocked"]
        _move(kernel, transaction, stage, target)
        if not ktm.CommitTransaction(transaction):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(transaction)
    assert target.read_bytes() == b"new"
    assert not stage.exists()


@pytest.mark.parametrize("commit, expected, stage_exists", [
    (False, b"old", True),
    (True, b"new", False),
])
def test_abrupt_helper_exit_preserves_transaction_outcome(tmp_path, commit, expected, stage_exists):
    _require_volume_support(tmp_path)
    target, stage = tmp_path / "target", tmp_path / "stage"
    target.write_bytes(b"old")
    stage.write_bytes(b"new")
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(target),
                             str(stage), "1" if commit else "0"],
                            capture_output=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert target.read_bytes() == expected
    assert stage.exists() is stage_exists


def test_running_executable_refuses_transactional_write(tmp_path):
    _require_volume_support(tmp_path)
    target = tmp_path / "running.exe"
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", target)
    process = subprocess.Popen([str(target), "-t", "127.0.0.1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        # The executable image is mapped before it can respond to output.
        time.sleep(0.3)
        assert process.poll() is None
        kernel, ktm = _api()
        transaction = _transaction(ktm)
        try:
            handle = _open(kernel, transaction, target)
            error = ctypes.get_last_error()
            if handle != _INVALID:
                kernel.CloseHandle(handle)
            assert handle == _INVALID and error == 32
        finally:
            kernel.CloseHandle(transaction)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


def test_existing_shared_writer_refuses_transactional_write(tmp_path):
    _require_volume_support(tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b"original")
    kernel, ktm = _api()
    writer = kernel.CreateFileW(str(target), 0xC0000000, 7, None, 3, 0, None)
    if writer == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        transaction = _transaction(ktm)
        try:
            handle = _open(kernel, transaction, target)
            error = ctypes.get_last_error()
            if handle != _INVALID:
                kernel.CloseHandle(handle)
            assert handle == _INVALID and error in {32, 6800}
        finally:
            kernel.CloseHandle(transaction)
    finally:
        kernel.CloseHandle(writer)
    assert target.read_bytes() == b"original"


if __name__ == "__main__":
    _abrupt_child(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "1")
