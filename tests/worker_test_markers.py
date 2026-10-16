"""Publish complete worker markers before a supervisor can trigger cancellation."""
from pathlib import Path
import json
import os
import sys


def process_birth(pid):
    """Read native Darwin start fields without process-local clock correction."""
    if sys.platform != "darwin":
        import psutil
        return psutil.Process(pid).create_time()
    import ctypes
    import ctypes.util
    import errno
    import psutil
    class BsdInfo(ctypes.Structure):
        _fields_ = ([(name, ctypes.c_uint32) for name in
                     ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "rfu")]
                    + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
                    + [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "pjobc", "e_tdev", "e_tpgid")]
                    + [("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)])
    library = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    info = BsdInfo()
    size = ctypes.sizeof(info)
    if size != 136:
        raise RuntimeError("unexpected Darwin process information layout")
    received = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
    if received <= 0:
        if ctypes.get_errno() == errno.ESRCH or not psutil.pid_exists(pid):
            raise psutil.NoSuchProcess(pid)
        raise OSError(ctypes.get_errno(), "Darwin process information unavailable")
    if received != size or info.pid != pid or info.start_sec <= 0 or info.start_usec >= 1_000_000:
        raise RuntimeError("invalid Darwin process information")
    return [info.start_sec, info.start_usec]


def publish_marker(path, value):
    path = Path(path)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(value, encoding="utf-8")
    os.replace(pending, path)


def publish_process_marker(path):
    """Bind the observation to this worker before cancellation can begin."""
    pid = os.getpid()
    publish_marker(path, json.dumps({"pid": pid, "birth": process_birth(pid)}))


def marked_process_exists(path):
    """A reused PID is not the marked worker; inspection errors propagate."""
    import psutil
    identity = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return process_birth(identity["pid"]) == identity["birth"]
    except psutil.NoSuchProcess:
        return False
