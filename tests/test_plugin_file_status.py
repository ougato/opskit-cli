import hashlib

import pytest

from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied
from core.plugin_host_files import file_status


def test_file_status_uses_directory_authorization_and_hash(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    seen = []
    def allow(scope, **kwargs):
        seen.append(scope)
        return require(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)
    monkeypatch.setattr(authority, "require", allow)
    options = {"authority": authority, "check_identity": lambda *args: True}
    assert file_status("cocos", "a" * 64, str(tmp_path), "build.json", **options) == {"exists": False, "sha256": None}
    (tmp_path / "build.json").write_bytes(b"test")
    assert file_status("cocos", "a" * 64, str(tmp_path), "build.json", **options) == {
        "exists": True, "sha256": hashlib.sha256(b"test").hexdigest()}
    assert seen[0].operation == "directory.read" and seen[0].resource == str(tmp_path)
    monkeypatch.setattr(authority, "require", lambda *a, **kw: (_ for _ in ()).throw(CapabilityDenied("denied")))
    with pytest.raises(CapabilityDenied):
        file_status("cocos", "a" * 64, str(tmp_path), "build.json", **options)
