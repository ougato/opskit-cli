import hashlib
import os
import time
from types import SimpleNamespace

import pytest

from core import plugin_artifact as artifact
from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied


def inspect(root, authority, **kwargs):
    return artifact.artifact_info("demo", "a" * 64, str(root), "build.bin",
        authority=authority, check_identity=lambda *args: True,
        deadline=time.monotonic() + 30, checkpoint=kwargs.get("checkpoint", lambda: None))


def authorize(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    original = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kwargs:
                        original(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs))
    return authority


def test_real_large_file_returns_only_size_and_checksum(tmp_path, monkeypatch):
    content = b"known synthetic artifact\0" * 100000
    (tmp_path / "build.bin").write_bytes(content)
    authority = authorize(tmp_path, monkeypatch)
    assert inspect(tmp_path, authority) == {
        "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    assert authority.list_grants() == []
    authority.close()
    with pytest.raises(CapabilityDenied):
        inspect(tmp_path, authority)


def test_batch_artifact_preserves_requested_order(tmp_path, monkeypatch):
    contents = {f"{index}.bin": str(index).encode() for index in range(artifact.MAX_BATCH)}
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    authority = authorize(tmp_path, monkeypatch)
    names = list(reversed(contents))
    try:
        assert artifact.artifact_batch_info("demo", "a" * 64, str(tmp_path), names,
            authority=authority, check_identity=lambda *args: True, deadline=time.monotonic() + 30,
            checkpoint=lambda: None) == [
                {"bytes": len(contents[name]), "sha256": hashlib.sha256(contents[name]).hexdigest()}
                for name in names
            ]
    finally:
        authority.close()


def test_denied_artifact_never_starts_file_worker(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    monkeypatch.setattr(authority, "require", lambda *args, **kwargs:
                        (_ for _ in ()).throw(CapabilityDenied("denied")))
    monkeypatch.setattr("network.worker.owned_process", lambda *args, **kwargs:
                        pytest.fail("denied request started worker"))
    with pytest.raises(CapabilityDenied):
        inspect(tmp_path, authority)


@pytest.mark.parametrize("relative", ["../secret", "/secret", "a:stream", "a\\secret"])
def test_invalid_path_rejected_before_prompt(tmp_path, relative):
    authority = SimpleNamespace(require=lambda *args, **kwargs: pytest.fail("escape prompted"))
    with pytest.raises(ValueError):
        artifact.artifact_info("demo", "a" * 64, str(tmp_path), relative, authority=authority,
            check_identity=lambda *args: True, deadline=time.monotonic() + 30, checkpoint=lambda: None)


def test_hardlinked_artifact_is_refused(tmp_path, monkeypatch):
    (tmp_path / "original").write_bytes(b"not an ordinary single-link artifact")
    os.link(tmp_path / "original", tmp_path / "build.bin")
    with pytest.raises(RuntimeError, match="inspection failed"):
        inspect(tmp_path, authorize(tmp_path, monkeypatch))


def test_oversized_file_is_refused_without_reading_body(tmp_path, monkeypatch):
    with (tmp_path / "build.bin").open("wb") as stream:
        stream.truncate(artifact.MAX_FILE + 1)
    with pytest.raises(RuntimeError, match="inspection failed"):
        inspect(tmp_path, authorize(tmp_path, monkeypatch))


def blocked_worker(connection, root, source, identity):
    read, write = os.pipe()
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def test_revocation_terminates_silent_hash_worker(tmp_path, monkeypatch):
    authority = authorize(tmp_path, monkeypatch)
    original = authority.check
    checks = []
    def check(scope, **kwargs):
        checks.append(scope)
        if len(checks) == 3:
            authority.revoke(scope)
        return original(scope, **kwargs)
    monkeypatch.setattr(authority, "check", check)
    monkeypatch.setattr(artifact, "_worker", blocked_worker)
    started = time.monotonic()
    with pytest.raises(CapabilityDenied):
        inspect(tmp_path, authority)
    assert len(checks) == 3 and time.monotonic() - started < 10


def test_broker_artifact_identity_comes_from_supervisor(tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    monkeypatch.setattr(sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(sandbox, "_remaining", lambda: 30)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda **kwargs: None)
    monkeypatch.setattr(sandbox._invocations, "deadline", time.monotonic() + 30, raising=False)
    authority = object()
    monkeypatch.setattr(sandbox._invocations, "capability_authority", authority, raising=False)
    def info(plugin, fingerprint, *args, **kwargs):
        assert plugin == "demo" and fingerprint == "a" * 64
        assert kwargs["authority"] is authority
        assert kwargs["check_identity"]("demo", fingerprint)
        assert not kwargs["check_identity"]("other", fingerprint)
        return {"bytes": 1, "sha256": "b" * 64}
    monkeypatch.setattr(artifact, "artifact_info", info)
    result = sandbox._broker(SimpleNamespace(name="demo"), {
        "rpc_version": 1, "type": "call", "method": "host.artifact_info",
        "args": [str(tmp_path), "build.bin"], "kwargs": {}})
    assert result["sha256"] == "b" * 64
