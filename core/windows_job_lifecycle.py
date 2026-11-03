"""Confirm all processes in an owned Windows Job have terminated."""
import ctypes
from ctypes import wintypes
import math
import time


class Accounting(ctypes.Structure):
    _fields_ = [("user_time", ctypes.c_longlong), ("kernel_time", ctypes.c_longlong),
                ("period_user_time", ctypes.c_longlong), ("period_kernel_time", ctypes.c_longlong),
                ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
                ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD)]


def terminate_and_wait(job, *, timeout=5):
    """Retain the caller's Job handle; failure must prevent successful commit."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("invalid Job cleanup allowance")
    deadline = time.monotonic() + timeout
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    terminate = kernel.TerminateJobObject
    terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate.restype = wintypes.BOOL
    query = kernel.QueryInformationJobObject
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    query.restype = wintypes.BOOL
    if not terminate(job, 1):
        raise ctypes.WinError(ctypes.get_last_error())
    while True:
        accounting = Accounting()
        if not query(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if accounting.active_processes == 0:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native Job cleanup unconfirmed")
        time.sleep(min(.01, remaining))
