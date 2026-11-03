"""Windows kernel CPU hard cap for an owned Job, applied before worker resume."""
import ctypes
from ctypes import wintypes


class CpuRate(ctypes.Structure):
    _fields_ = [("flags", wintypes.DWORD), ("rate", wintypes.DWORD)]


def set_cpu_limit(job, percent):
    if type(percent) is not int or not 1 <= percent <= 100:
        raise ValueError("invalid Job CPU limit")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    # ENABLE | HARD_CAP; rate is hundredths of one percent across the whole Job.
    rate = CpuRate(0x1 | 0x4, percent * 100)
    if not kernel.SetInformationJobObject(job, 15, ctypes.byref(rate), ctypes.sizeof(rate)):
        raise ctypes.WinError(ctypes.get_last_error())
