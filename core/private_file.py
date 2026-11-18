"""Read-only private-file permission checks against the opened object."""
from contextlib import contextmanager
import errno
import os
import sys
import tempfile
import uuid


@contextmanager
def temporary_private_file():
    """An owned binary file, protected before payload writes and closed on exit.

    Windows tempfile defaults inherit the temporary directory's ACL. Create an
    exclusive delete-on-close object with an explicit ACL instead. Privileged
    administrators/SYSTEM and same-account processes remain trusted.
    """
    with (_windows_temporary_file() if os.name == "nt" else tempfile.TemporaryFile()) as stream:
        verify_private_file(stream.fileno())
        yield stream


def _windows_temporary_file():
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer]
    convert.restype = wintypes.BOOL
    free = kernel.LocalFree
    free.argtypes = [pointer]
    free.restype = pointer

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", pointer),
                    ("inherit", wintypes.BOOL)]

    security = pointer()
    if not convert("D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)", 1,
                   ctypes.byref(security), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), security, False)
        name = os.path.join(tempfile.gettempdir(), ".opskit-private-" + uuid.uuid4().hex)
        # CREATE_NEW, no sharing, temporary + open-reparse-point + delete-on-close.
        handle = create(name, 0xC0030000, 0, ctypes.byref(attributes), 1, 0x04200100, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free(security)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        return os.fdopen(descriptor, "w+b")
    except BaseException:
        os.close(descriptor)
        raise


def verify_private_file(descriptor):
    if os.name == "nt":
        _windows_permissions(descriptor)
        return
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("secret input requires owner-only permissions")
    if sys.platform == "darwin":
        _darwin_permissions(descriptor)
    elif not sys.platform.startswith("linux"):
        raise ValueError("private file permission checks unsupported on this platform")


def _darwin_permissions(descriptor):
    import ctypes
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    get_acl = library.acl_get_fd_np
    get_acl.argtypes = [ctypes.c_int, ctypes.c_int]
    get_acl.restype = ctypes.c_void_p
    free = library.acl_free
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = get_acl(descriptor, 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        # Darwin reports ENOENT for an absent extended ACL on a valid fd.
        # The caller checks the same open object's identity before and after.
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise ValueError("cannot verify secret input ACL")
    try:
        # A present Darwin ACL is rejected as a whole, even if restrictive or
        # empty. This avoids approximating ordered ACL semantics or confusing
        # Darwin's acl_get_entry success=0 with other systems' success=1.
        raise ValueError("secret input must have no extended ACL")
    finally:
        free(acl)


def _windows_permissions(descriptor, *, handle=None, directory=False):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    get_security = advapi.GetSecurityInfo
    get_security.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                            ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer),
                            pointer, ctypes.POINTER(pointer)]
    get_security.restype = wintypes.DWORD
    open_token = advapi.OpenProcessToken
    open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_token.restype = wintypes.BOOL
    token_info = advapi.GetTokenInformation
    token_info.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD)]
    token_info.restype = wintypes.BOOL
    convert_sid = advapi.ConvertSidToStringSidW
    convert_sid.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    convert_sid.restype = wintypes.BOOL
    valid_sid = advapi.IsValidSid
    valid_sid.argtypes = [pointer]
    valid_sid.restype = wintypes.BOOL
    sid_length = advapi.GetLengthSid
    sid_length.argtypes = [pointer]
    sid_length.restype = wintypes.DWORD
    get_info = advapi.GetAclInformation
    get_info.argtypes = [pointer, pointer, wintypes.DWORD, ctypes.c_int]
    get_info.restype = wintypes.BOOL
    get_ace = advapi.GetAce
    get_ace.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    get_ace.restype = wintypes.BOOL
    free = kernel.LocalFree
    free.argtypes = [pointer]
    free.restype = pointer
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    class TokenUser(ctypes.Structure):
        _fields_ = [("sid", pointer), ("attributes", wintypes.DWORD)]

    class AclSize(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("used", wintypes.DWORD),
                    ("free", wintypes.DWORD)]

    class AceHeader(ctypes.Structure):
        _fields_ = [("kind", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
                    ("size", wintypes.WORD)]

    def sid_text(sid):
        value = wintypes.LPWSTR()
        if not valid_sid(sid) or not convert_sid(sid, ctypes.byref(value)):
            raise ValueError("cannot verify secret input ACL identity")
        try:
            return value.value
        finally:
            free(ctypes.cast(value, pointer))

    token = wintypes.HANDLE()
    if not open_token(wintypes.HANDLE(-1), 8, ctypes.byref(token)):  # TOKEN_QUERY
        raise ValueError("cannot verify current secret input identity")
    try:
        size = wintypes.DWORD()
        token_info(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not 0 < size.value <= 65536:
            raise ValueError("invalid current secret input identity")
        buffer = ctypes.create_string_buffer(size.value)
        if not token_info(token, 1, buffer, size, ctypes.byref(size)):
            raise ValueError("cannot verify current secret input identity")
        user = sid_text(TokenUser.from_buffer(buffer).sid)
    finally:
        close(token)

    owner, dacl, security = pointer(), pointer(), pointer()
    if handle is None:
        handle = msvcrt.get_osfhandle(descriptor)
    code = get_security(handle, 1, 5,
                        ctypes.byref(owner), None, ctypes.byref(dacl), None,
                        ctypes.byref(security))  # SE_FILE_OBJECT; OWNER|DACL
    if code:
        raise ValueError("cannot verify secret input permissions")
    try:
        # SYSTEM and administrators already form the machine's privileged
        # boundary. OWNER RIGHTS is allowed only after validating the owner.
        trusted = {user, "S-1-5-18", "S-1-5-32-544"}
        if not dacl or sid_text(owner) not in trusted:
            raise ValueError("secret input requires a private owner and ACL")
        info = AclSize()
        if not get_info(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise ValueError("cannot verify secret input ACL")
        if info.count > 4096 or info.used > 65535:
            raise ValueError("secret input ACL exceeds limit")
        for index in range(info.count):
            ace = pointer()
            if not get_ace(dacl, index, ctypes.byref(ace)):
                raise ValueError("cannot verify secret input ACL entry")
            header = AceHeader.from_address(ace.value)
            if header.kind == 1:  # ACCESS_DENIED_ACE never grants access.
                continue
            if header.kind != 0 or header.size < 16:
                raise ValueError("unsupported secret input ACL entry")
            sid = ace.value + 8  # Header, access mask, then the SID.
            if not valid_sid(sid) or sid_length(sid) + 8 != header.size:
                raise ValueError("invalid secret input ACL identity")
            identity = sid_text(sid)
            if identity not in trusted | {"S-1-3-4"}:
                # Directory readers cannot replace entries. Refuse any foreign
                # write/delete/ownership grant, including generic write/all.
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                if directory and not mask & 0x500D0156:
                    continue
                raise ValueError("secret input ACL grants access to another identity")
    finally:
        free(security)
