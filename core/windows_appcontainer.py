"""Windows AppContainer profile primitive; no capability SIDs are granted.

Profiles alone do not isolate a process. The process launcher must apply
SECURITY_CAPABILITIES, restrict inherited handles, and assign a Job before resume.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import os
import math
import subprocess

from core.native_sandbox import NativeLimits, NativeSandboxError


class SecurityCapabilities(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("capabilities", ctypes.c_void_p),
                ("count", wintypes.DWORD), ("reserved", wintypes.DWORD)]


class StartupInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
                ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
                ("x", wintypes.DWORD), ("y", wintypes.DWORD),
                ("width", wintypes.DWORD), ("height", wintypes.DWORD),
                ("chars_x", wintypes.DWORD), ("chars_y", wintypes.DWORD),
                ("fill", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
                ("reserved_data", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
                ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


class WindowsProcess:
    """Borrowed process handle; valid only inside WindowsBackend.launch()."""

    def __init__(self, kernel, info):
        self.kernel = kernel
        self.info = info
        self.pid = info.pid
        self.closed = False

    def poll(self):
        if self.closed:
            raise RuntimeError("native process handle is closed")
        status = self.kernel.WaitForSingleObject(self.info.process, 0)
        if status == 258:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.info.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def wait(self, timeout):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid native process wait allowance")
        if self.closed:
            raise RuntimeError("native process handle is closed")
        status = self.kernel.WaitForSingleObject(self.info.process, math.ceil(timeout * 1000))
        if status == 258:
            from core.i18n import t
            raise TimeoutError(t("plugin_native.errors.execution_timeout"))
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()


class WindowsBackend:
    platform = "win32"

    def _api(self):
        if os.name != "nt":
            raise NativeSandboxError("windows_required")
        try:
            api = ctypes.WinDLL("userenv", use_last_error=True)
            api.CreateAppContainerProfile.argtypes = [
                wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ]
            api.CreateAppContainerProfile.restype = ctypes.c_long
            api.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
            api.DeleteAppContainerProfile.restype = ctypes.c_long
            free = ctypes.WinDLL("advapi32", use_last_error=True).FreeSid
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
            return api, free
        except (OSError, AttributeError) as error:
            raise NativeSandboxError("windows_api_missing") from error

    def probe(self) -> None:
        self._api()

    def run_probe(self, executable: str, args: list[str], *, timeout: float = 5,
                  staging=None, writable: bool = False, _stdio=None) -> int:
        """Run an output-free native probe, verifying token isolation before resume.

        This is not the plugin transport: no inherited handles, project access,
        network capabilities or caller environment are provided.
        """
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("invalid AppContainer probe allowance")
        trees = () if staging is None else (staging,)
        with self.launch(executable, args, readonly_trees=() if writable else trees,
                         writable_trees=trees if writable else (), stdio=_stdio) as child:
            return child.wait(timeout)

    @contextmanager
    def launch(self, executable: str, args: list[str], *, readonly_trees=(),
               writable_trees=(), stdio=None, environment=None, cwd=None, limits=None):
        """Yield a verified, Job-managed child; close kills all owned descendants.

        File trees must be host-owned staging, never arbitrary plugin-supplied paths.
        Only explicitly listed standard handles are inherited.
        """
        limits = NativeLimits() if limits is None else limits
        if not isinstance(limits, NativeLimits):
            raise ValueError("invalid native resource limits")
        self.probe()
        if (not os.path.isabs(executable) or not os.path.isfile(executable)
                or any(not isinstance(arg, str) or "\0" in arg for arg in args)):
            raise ValueError("invalid AppContainer probe request")
        allowed_environment = {"OPSKIT_SANDBOX", "OPSKIT_LANGUAGE", "OPSKIT_DATA_DIR", "OPSKIT_PLUGIN_ROOT",
                               "OPSKIT_SEED_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING"}
        if environment is not None and (not isinstance(environment, dict)
                or set(environment) - allowed_environment
                or any(not isinstance(value, str) or "\0" in value for value in environment.values())):
            raise ValueError("invalid native worker environment")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                              wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                                     ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                                     ctypes.c_void_p]
        kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                          ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
                                          ctypes.POINTER(ProcessInfo)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                           wintypes.BOOL, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        with self.profile() as (_, sid), ExitStack() as access:
            from core.windows_sandbox_acl import staged_access
            for tree in readonly_trees:
                access.enter_context(staged_access(tree, sid))
            for tree in writable_trees:
                access.enter_context(staged_access(tree, sid, writable=True))
            size = ctypes.c_size_t()
            attribute_count = 2 if stdio is not None else 1
            kernel.InitializeProcThreadAttributeList(None, attribute_count, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise NativeSandboxError("attributes_invalid")
            storage = ctypes.create_string_buffer(size.value)
            if not kernel.InitializeProcThreadAttributeList(storage, attribute_count, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            process = ProcessInfo()
            job = None
            child = None
            inherited = ExitStack()
            try:
                capabilities = SecurityCapabilities(sid, None, 0, 0)
                if not kernel.UpdateProcThreadAttribute(storage, 0, 0x20009,
                        ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
                startup = StartupInfoEx()
                startup.startup.size = ctypes.sizeof(startup)
                startup.attributes = ctypes.cast(storage, ctypes.c_void_p)
                if stdio is not None:
                    if len(stdio) != 3:
                        raise ValueError("native standard streams require exactly three handles")
                    duplicates = {}
                    current_process = kernel.GetCurrentProcess()
                    for handle in stdio:
                        if handle not in duplicates:
                            duplicate = wintypes.HANDLE()
                            if not kernel.DuplicateHandle(current_process, handle, current_process,
                                    ctypes.byref(duplicate), 0, True, 2):
                                raise ctypes.WinError(ctypes.get_last_error())
                            duplicates[handle] = duplicate.value
                            inherited.callback(kernel.CloseHandle, duplicate)
                    handles = (wintypes.HANDLE * len(duplicates))(*duplicates.values())
                    if not kernel.UpdateProcThreadAttribute(storage, 0, 0x20002,
                            handles, ctypes.sizeof(handles), None, None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    startup.startup.flags = 0x100
                    startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = (
                        duplicates[handle] for handle in stdio)
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline([executable, *args]))
                # Windows loader requires SystemRoot; do not inherit user secrets.
                root_buffer = ctypes.create_unicode_buffer(32768)
                kernel.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
                root_length = kernel.GetWindowsDirectoryW(root_buffer, len(root_buffer))
                if not 0 < root_length < len(root_buffer):
                    raise NativeSandboxError("system_directory_missing")
                safe_environment = {"SystemRoot": root_buffer.value}
                for key in ("SystemDrive", "WINDIR", "TEMP", "TMP", "USERPROFILE",
                            "LOCALAPPDATA", "APPDATA"):
                    if os.environ.get(key):
                        safe_environment[key] = os.environ[key]
                safe_environment.update(environment or {})
                environment_block = ctypes.create_unicode_buffer("\0".join(
                    key + "=" + value for key, value in sorted(safe_environment.items())
                ) + "\0\0")
                if not kernel.CreateProcessW(executable, command, None, None, stdio is not None,
                        0x80000 | 0x4 | 0x400 | 0x08000000, environment_block,
                        str(cwd) if cwd is not None else os.path.dirname(executable),
                        ctypes.byref(startup), ctypes.byref(process)):
                    raise ctypes.WinError(ctypes.get_last_error())
                inherited.close()
                token = wintypes.HANDLE()
                if not advapi.OpenProcessToken(process.process, 8, ctypes.byref(token)):
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    isolated = wintypes.DWORD()
                    returned = wintypes.DWORD()
                    if not advapi.GetTokenInformation(token, 29, ctypes.byref(isolated),
                            ctypes.sizeof(isolated), ctypes.byref(returned)) or isolated.value != 1:
                        raise NativeSandboxError("token_not_isolated")
                finally:
                    kernel.CloseHandle(token)
                from core.windows_job import attach_running, close
                job = attach_running(process.process, memory_bytes=limits.memory_bytes,
                                     max_processes=limits.max_processes)
                from core.windows_job_cpu import set_cpu_limit
                set_cpu_limit(job, limits.cpu_percent)
                if kernel.ResumeThread(process.thread) == 0xffffffff:
                    raise ctypes.WinError(ctypes.get_last_error())
                child = WindowsProcess(kernel, process)
                yield child
            finally:
                try:
                    inherited.close()
                finally:
                    if child is not None:
                        child.closed = True
                    try:
                        if job is not None:
                            from core.windows_job_lifecycle import terminate_and_wait
                            try:
                                terminate_and_wait(job)
                            finally:
                                close(job)
                    finally:
                        # Release every handle even when tree confirmation fails.
                        with ExitStack() as disposal:
                            disposal.callback(kernel.DeleteProcThreadAttributeList, storage)
                            if process.thread:
                                disposal.callback(kernel.CloseHandle, process.thread)
                            if process.process:
                                disposal.callback(kernel.CloseHandle, process.process)
                                kernel.TerminateProcess(process.process, 1)
                                if kernel.WaitForSingleObject(process.process, 5000) != 0:
                                    raise NativeSandboxError("transport_cleanup_failed")

    @contextmanager
    def profile(self):
        """Create a leased profile; abnormal exit retains a bound recovery record."""
        from core.windows_profile_recovery import managed_profile
        with managed_profile(*self._api()) as profile:
            yield profile
