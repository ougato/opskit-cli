import base64

import pytest

from core.plugin_host_files import read_file
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied
from core.native_sandbox import GrantChoice


def test_host_read_requires_exact_authorized_scope(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.json").write_bytes(b'{"version":1}')
    authority = CapabilityAuthority(path=tmp_path / "permissions.yaml")
    original = authority.require
    prompts = []
    def require(scope, **kwargs):
        prompts.append(scope)
        return original(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)
    monkeypatch.setattr(authority, "require", require)
    reply = read_file("demo", "a" * 64, str(root), "config.json",
                      authority=authority, check_identity=lambda *a: True)
    assert base64.b64decode(reply["content"]) == b'{"version":1}'
    assert prompts[0].operation == "directory.read"
    assert prompts[0].resource == str(root)
    authority.close()
    with pytest.raises(CapabilityDenied):
        read_file("demo", "a" * 64, str(root), "config.json",
                  authority=authority, check_identity=lambda *a: True)


@pytest.mark.parametrize("relative", ["../secret", "/secret", "C:/secret", "a:stream", "folder\\secret"])
def test_host_read_rejects_escape_before_prompt(tmp_path, relative):
    class NoAuthority:
        def require(self, *a, **k):
            pytest.fail("invalid path prompted")
    with pytest.raises(ValueError):
        read_file("demo", "a" * 64, str(tmp_path), relative,
                  authority=NoAuthority(), check_identity=lambda *a: True)


def test_host_read_denial_never_reads_file(tmp_path, monkeypatch):
    from core import plugin_host_files
    authority = CapabilityAuthority(path=tmp_path / "permissions.yaml")
    monkeypatch.setattr(authority, "require", lambda *a, **k: (_ for _ in ()).throw(CapabilityDenied("denied")))
    monkeypatch.setattr(plugin_host_files, "_read_file", lambda *a: pytest.fail("denied request read file"))
    with pytest.raises(CapabilityDenied):
        read_file("demo", "a" * 64, str(tmp_path), "secret",
                  authority=authority, check_identity=lambda *a: True)


def test_directory_replaced_during_authorization_never_reads(tmp_path, monkeypatch):
    from core import plugin_host_files
    root = tmp_path / "project"
    root.mkdir()
    (root / "secret").write_bytes(b"original")

    class Authority:
        def require(self, *args, **kwargs):
            root.rename(tmp_path / "previous")
            root.mkdir()
            (root / "secret").write_bytes(b"substituted")

        def check(self, *args, **kwargs):
            pass

    monkeypatch.setattr(plugin_host_files, "_read_file", lambda *a: pytest.fail("replacement read"))
    with pytest.raises(ValueError, match="changed during authorization"):
        read_file("demo", "a" * 64, str(root), "secret",
                  authority=Authority(), check_identity=lambda *a: True)


def test_broker_uses_supervisor_identity_and_scoped_authority(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import plugin_sandbox as sandbox
    from core import plugin_host_files
    monkeypatch.setattr(sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(sandbox, "_remaining", lambda: 10)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda **k: None)
    authority = object()
    monkeypatch.setattr(sandbox._invocations, "capability_authority", authority, raising=False)
    calls = []
    def read(plugin, fingerprint, directory, relative, **kwargs):
        calls.append((plugin, fingerprint, directory, relative))
        assert kwargs["authority"] is authority
        assert kwargs["check_identity"]("demo", "a" * 64)
        assert not kwargs["check_identity"]("other", "a" * 64)
        return {"encoding": "base64", "content": ""}
    monkeypatch.setattr(plugin_host_files, "read_file", read)
    request = {"rpc_version": 1, "type": "call", "method": "host.read_file",
               "args": [str(tmp_path), "config.json"], "kwargs": {}}
    assert sandbox._broker(SimpleNamespace(name="demo"), request)["encoding"] == "base64"
    assert calls == [("demo", "a" * 64, str(tmp_path), "config.json")]
