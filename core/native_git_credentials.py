"""Read one exact Windows Git credential; no helpers or shell execution."""
import base64
import os


def https_material(source):
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    host = f"[{source.host}]" if ":" in source.host else source.host
    authority = host if source.port == 443 else f"{host}:{source.port}"
    # Prefer the repository-specific entry, then the same HTTPS server's entry.
    for target in (f"git:https://{authority}{source.path}", f"git:https://{authority}"):
        value = ctypes.POINTER(Credential)()
        if not api.CredReadW(target, 1, 0, ctypes.byref(value)):
            continue
        try:
            item = value.contents
            if not 0 < item.CredentialBlobSize <= 16384 or not item.UserName:
                continue
            password = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode("utf-16-le")
            username = item.UserName
            if ":" in username or any(c in username + password for c in "\r\n\x00"):
                continue
            encoded = base64.b64encode((username + ":" + password).encode()).decode("ascii")
            return {"authorization": "Basic " + encoded}
        except (UnicodeError, ValueError):
            continue
        finally:
            api.CredFree(value)
    return None
