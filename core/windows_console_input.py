"""Inspect pending Windows console keys without consuming or blocking on them."""
import ctypes
from ctypes import wintypes
import io
import sys


class _KeyEvent(ctypes.Structure):
    _fields_ = [("key_down", wintypes.BOOL), ("repeat_count", wintypes.WORD),
                ("virtual_key", wintypes.WORD), ("scan_code", wintypes.WORD),
                ("character", wintypes.WCHAR), ("control_state", wintypes.DWORD)]


class _Event(ctypes.Union):
    _fields_ = [("key", _KeyEvent), ("other", ctypes.c_byte * 16)]


class _InputRecord(ctypes.Structure):
    _fields_ = [("event_type", wintypes.WORD), ("event", _Event)]


def _has_key(records):
    for record in records:
        if record.event_type != 1 or not record.event.key.key_down:
            continue
        key = record.event.key
        # Characters include Enter, Backspace and Ctrl combinations. The
        # remaining keys match the extended navigation/function keys getwch
        # returns as two-character sequences.
        if (ord(key.character) or 0x21 <= key.virtual_key <= 0x28
                or key.virtual_key in (0x2d, 0x2e) or 0x70 <= key.virtual_key <= 0x7b):
            return True
    return False


def key_available():
    """Find a pending key even when release events precede it in the queue."""
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = kernel.PeekConsoleInputW
    peek.argtypes = [wintypes.HANDLE, ctypes.POINTER(_InputRecord), wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    records = (_InputRecord * 1024)()
    count = wintypes.DWORD()
    if not peek(handle, records, len(records), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return _has_key(records[:count.value])
