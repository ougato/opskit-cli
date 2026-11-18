"""Exact-target Windows credential lookup without invoking Git helpers."""
import base64
import ctypes
import os
from types import SimpleNamespace

import pytest
from core.native_git_credentials import https_material
from core.plugin_fetch_source import remote_source


@pytest.mark.skipif(os.name != "nt", reason="Windows credential API")
@pytest.mark.parametrize("port", [443, 8443])
def test_windows_credentials_are_exact_scoped_and_freed(monkeypatch, port):
    requested, released, retained = [], [], []
    def read(target, kind, flags, output):
        requested.append(target)
        if len(requested) == 1:
            return False
        credential = output._obj._type_()
        blob = ctypes.create_string_buffer("synthetic-token".encode("utf-16-le"))
        credential.CredentialBlobSize = len(blob.raw) - 1
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.UserName = "fixture-user"
        pointer = ctypes.pointer(credential)
        ctypes.cast(output, ctypes.POINTER(type(pointer)))[0] = pointer
        retained.extend([credential, blob, pointer])
        return True
    def free(value):
        released.append(True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: SimpleNamespace(CredReadW=read, CredFree=free))
    result = https_material(remote_source(f"https://example.com:{port}/team/repo.git"))
    authority = "example.com" if port == 443 else "example.com:8443"
    assert requested == [f"git:https://{authority}/team/repo.git", f"git:https://{authority}"]
    assert released == [True]
    assert result == {"authorization": "Basic " + base64.b64encode(b"fixture-user:synthetic-token").decode()}


@pytest.mark.parametrize("explicit", [False, True])
def test_https_only_uses_local_credentials_after_denial(monkeypatch, explicit):
    from contextlib import contextmanager
    import io
    from core import native_git_https as transport
    from core import native_git_credentials as credentials
    from core.native_git_ssh import SSHFailure
    calls, lookups = [], []
    @contextmanager
    def request(source, material, allow_private, method, suffix, body=b""):
        calls.append(dict(material))
        if "authorization" not in material:
            raise SSHFailure("fetch_http_auth_failed")
        yield io.BytesIO(b"fixture")
    monkeypatch.setattr(transport, "_request", request)
    monkeypatch.setattr(transport, "authentication", lambda *a: SimpleNamespace(payload=lambda: {}))
    def lookup(source):
        lookups.append(source.url)
        return {"authorization": "fixture-secret"}
    monkeypatch.setattr(credentials, "https_material", lookup)
    monkeypatch.setattr(transport, "advertisement", lambda *a: ("selection", b"request"))
    monkeypatch.setattr(transport, "receive_pack", lambda *a: "pack")
    if explicit:
        with pytest.raises(SSHFailure):
            transport._download("https://example.com/team/repo", {}, False, io.BytesIO(), None)
        assert not lookups and calls == [{}]
    else:
        assert transport._download("https://example.com/team/repo", None, False, io.BytesIO(), None) == "pack"
        assert lookups == ["https://example.com:443/team/repo"]
        assert calls == [{}, {"authorization": "fixture-secret"}, {"authorization": "fixture-secret"}]
