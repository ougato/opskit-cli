import base64
import hashlib
import os

import pytest

from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied
from core.plugin_host_write import compare_write, create_file


@pytest.fixture
def context(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "permissions.yaml")
    require = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kw: require(scope, prompt=lambda _: GrantChoice.ONCE, **kw))
    return {"authority": authority, "check_identity": lambda *a: True, "checkpoint": lambda: None}


def request(root, old, new, context):
    return compare_write("demo", "a" * 64, str(root), "env.json", hashlib.sha256(old).hexdigest(),
                         base64.b64encode(new).decode(), **context)


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive file backend")
def test_write_requires_scope_and_expected_original(tmp_path, context):
    path = tmp_path / "env.json"
    path.write_bytes(b"old")
    with pytest.raises(ValueError, match="contents changed"):
        request(tmp_path, b"wrong", b"new", context)
    assert path.read_bytes() == b"old"
    assert request(tmp_path, b"old", b"new", context)["sha256"] == hashlib.sha256(b"new").hexdigest()
    assert path.read_bytes() == b"new"


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive file backend")
def test_denial_does_not_write(tmp_path, context, monkeypatch):
    path = tmp_path / "env.json"
    path.write_bytes(b"old")
    def deny(*args, **kwargs):
        raise CapabilityDenied("denied")
    monkeypatch.setattr(context["authority"], "require", deny)
    with pytest.raises(CapabilityDenied):
        request(tmp_path, b"old", b"new", context)
    assert path.read_bytes() == b"old"


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive file backend")
def test_revocation_after_write_restores_original(tmp_path, context, monkeypatch):
    path = tmp_path / "env.json"
    path.write_bytes(b"old")
    checks = 0
    real_check = context["authority"].check
    def check(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 3:
            raise PermissionError("revoked")
        return real_check(*args, **kwargs)
    monkeypatch.setattr(context["authority"], "check", check)
    with pytest.raises(PermissionError, match="revoked"):
        request(tmp_path, b"old", b"new", context)
    assert path.read_bytes() == b"old"


def test_relative_escape_rejected_before_consent(tmp_path, context, monkeypatch):
    monkeypatch.setattr(context["authority"], "require", lambda *a, **k: pytest.fail("invalid path prompted"))
    with pytest.raises(ValueError):
        compare_write("demo", "a" * 64, str(tmp_path), "../env.json", "0" * 64, "", **context)


@pytest.mark.skipif(os.name != "nt", reason="Windows file creation backend")
def test_create_publishes_whole_file_and_never_overwrites(tmp_path, context):
    encoded = base64.b64encode(b'{"version": 1}').decode()
    result = create_file("demo", "a" * 64, str(tmp_path), "build.json", encoded, **context)
    assert result["sha256"] == hashlib.sha256(b'{"version": 1}').hexdigest()
    assert (tmp_path / "build.json").read_bytes() == b'{"version": 1}'
    with pytest.raises(FileExistsError):
        create_file("demo", "a" * 64, str(tmp_path), "build.json", encoded, **context)
    assert (tmp_path / "build.json").read_bytes() == b'{"version": 1}'
    assert not list(tmp_path.glob(".opskit-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows file creation backend")
def test_create_revoked_before_publish_removes_own_temporary(tmp_path, context, monkeypatch):
    checks = 0
    real_check = context["authority"].check
    def check(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise PermissionError("revoked")
        return real_check(*args, **kwargs)
    monkeypatch.setattr(context["authority"], "check", check)
    with pytest.raises(PermissionError):
        create_file("demo", "a" * 64, str(tmp_path), "build.json", "e30=", **context)
    assert not (tmp_path / "build.json").exists()
    assert not list(tmp_path.glob(".opskit-*.tmp"))
