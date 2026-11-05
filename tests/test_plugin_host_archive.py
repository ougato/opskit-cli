import json
import struct

import pytest

from core import plugin_host_archive
from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority


def _archive(path, tree):
    content = json.dumps(tree).encode()
    padded = (len(content) + 3) & ~3
    size = 8 + padded
    path.write_bytes(struct.pack("<4I", 4, size, size - 4, len(content)) + content + bytes(padded - len(content)))


def test_archive_lists_only_directories_after_host_read_authorization(tmp_path, monkeypatch):
    _archive(tmp_path / "app.asar", {"files": {"extensions": {"files": {
        "web-mobile": {"files": {}}, "android": {"files": {}},
        "file": {"size": 1, "offset": "0"}, "link": {"link": "other"},
    }}}})
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    scopes = []
    def approve(scope, **kwargs):
        scopes.append(scope)
        return require(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)
    monkeypatch.setattr(authority, "require", approve)
    result = plugin_host_archive.directories("demo", "a" * 64, str(tmp_path), "app.asar", "extensions",
        authority=authority, check_identity=lambda *a: True, checkpoint=lambda: None)
    assert result == ["android", "web-mobile"]
    assert [(scope.operation, scope.resource) for scope in scopes] == [("directory.read", str(tmp_path))]
    assert plugin_host_archive.directories("demo", "a" * 64, str(tmp_path), "app.asar", "missing/extensions",
        authority=authority, check_identity=lambda *a: True, checkpoint=lambda: None) == []


def test_archive_rejects_invalid_header_and_denied_scope(tmp_path, monkeypatch):
    archive = tmp_path / "app.asar"
    archive.write_bytes(b"bad")
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    monkeypatch.setattr(authority, "require", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError):
        plugin_host_archive.directories("demo", "a" * 64, str(tmp_path), "app.asar", "extensions",
            authority=authority, check_identity=lambda *a: True, checkpoint=lambda: None)
    monkeypatch.setattr(authority, "require", lambda *a, **kw: None)
    monkeypatch.setattr(authority, "check", lambda *a, **kw: None)
    with pytest.raises(ValueError):
        plugin_host_archive.directories("demo", "a" * 64, str(tmp_path), "app.asar", "extensions",
            authority=authority, check_identity=lambda *a: True, checkpoint=lambda: None)
