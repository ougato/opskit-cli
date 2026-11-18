"""Private profiles use existing identity/permission checks in owned workers."""
import base64
import json
import os

import pytest

from core import plugin_fetch_profile as profiles
from core.cli_secrets import export_secret
from core.secrets import restrict_permissions
from tests.worker_test_markers import marked_process_exists, publish_process_marker

SOURCE = "https://example.com/org/repo.git"
PROFILE = {"schema_version": 1, "source": SOURCE,
           "authentication": {"kind": "bearer", "token": "fixture-private-token"}}


def private_file(tmp_path, raw=None):
    path = tmp_path / "profile.json"
    export_secret(str(path), PROFILE)
    if raw is not None:
        path.write_bytes(raw)
    return path


def test_private_profile_loads_without_modification(tmp_path):
    path = private_file(tmp_path)
    before = path.read_bytes(), path.stat().st_mode
    assert profiles.load_profile(SOURCE, str(path), timeout=30) == PROFILE
    assert (path.read_bytes(), path.stat().st_mode) == before


def test_full_input_budget_crosses_four_frames(tmp_path):
    raw = json.dumps(PROFILE).encode()
    path = private_file(tmp_path, raw + b" " * (65536 - len(raw)))
    assert profiles.load_profile(SOURCE, str(path), timeout=30) == PROFILE


@pytest.mark.parametrize("raw", [b"fixture-private-token", b'{}', b'null', b'\xff', b'{"schema_version":1,"schema_version":1}', b"x" * 65537],
                         ids=["not-json", "empty-object", "null", "invalid-utf8", "duplicate-fields", "oversize"])
def test_bad_inputs_refused_without_sensitive_errors(tmp_path, raw):
    path = private_file(tmp_path, raw)
    with pytest.raises(ValueError, match="fetch profile input rejected") as error:
        profiles.load_profile(SOURCE, str(path), timeout=30)
    assert "fixture-private-token" not in str(error.value)


def test_full_source_mismatch_refused(tmp_path):
    path = private_file(tmp_path)
    with pytest.raises(ValueError, match="input rejected"):
        profiles.load_profile(SOURCE + "-other", str(path), timeout=30)


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "directory"])
def test_linked_or_nonregular_profile_refused(tmp_path, kind):
    path = private_file(tmp_path)
    if kind == "hardlink":os.link(path, tmp_path / "alias")
    elif kind == "symlink":
        target = tmp_path / "original"
        path.rename(target)
        try:path.symlink_to(target)
        except OSError:pytest.skip("native symlink unavailable")
    else:path.unlink();path.mkdir()
    with pytest.raises(ValueError, match="input rejected"):
        profiles.load_profile(SOURCE, str(path), timeout=30)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode fixture; Windows ACL covered by shared secret input suite")
def test_shared_profile_refused_without_permission_repair(tmp_path):
    path = private_file(tmp_path);path.chmod(0o644)
    with pytest.raises(ValueError, match="input rejected"):
        profiles.load_profile(SOURCE, str(path), timeout=30)
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("filename,timeout", [("-", 30), ("", 30), ("x\x00y", 30), (None, 30), ("file", True), ("file", 0), ("file", float("inf"))])
def test_invalid_request_never_starts_reader(filename, timeout, monkeypatch):
    from network import worker
    monkeypatch.setattr(worker, "owned_process", lambda *a, **k: pytest.fail("invalid input started reader"))
    with pytest.raises(ValueError, match="input"):
        profiles.load_profile(SOURCE, filename, timeout=timeout)


def blocked_worker(connection, *args):
    from core import cli_secrets
    from network import worker
    original_send = worker.send
    def block():
        read, write = os.pipe();publish_process_marker(os.environ["OPSKIT_PROFILE_MARKER"])
        try:os.read(read, 1)
        finally:os.close(read);os.close(write)
    def read(path):block()
    def send(connection, result):
        if "page" in result:block()
        return original_send(connection, result)
    if os.environ["OPSKIT_PROFILE_STAGE"] == "read":cli_secrets._read_secret_bytes = read
    else:worker.send = send
    profiles._profile_worker(connection, *args)


@pytest.mark.parametrize("stage", ["read", "page"])
def test_blocking_read_or_transfer_expires_with_worker_gone(tmp_path, monkeypatch, expire_worker_after_marker, stage):
    path = private_file(tmp_path);marker = tmp_path / "worker.json"
    monkeypatch.setenv("OPSKIT_PROFILE_STAGE", stage);monkeypatch.setenv("OPSKIT_PROFILE_MARKER", str(marker))
    monkeypatch.setattr(profiles, "_profile_worker", blocked_worker)
    timeout, observed = expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError):profiles.load_profile(SOURCE, str(path), timeout=timeout)
    assert observed and not marked_process_exists(marker)


def changed_worker(connection, *args):
    from core import cli_secrets
    original = cli_secrets.os.fdopen
    filename = args[1]
    class Changed:
        def __init__(self, *a, **kw):self.stream = original(*a, **kw)
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self, *args):return self.stream.__exit__(*args)
        def read(self, *args):
            raw = self.stream.read(*args)
            from pathlib import Path
            target = Path(filename);replacement = target.with_suffix('.replacement')
            replacement.write_bytes(raw);restrict_permissions(replacement)
            os.replace(replacement, target)
            return raw
    cli_secrets.os.fdopen = Changed
    profiles._profile_worker(connection, *args)


def test_replacement_during_read_refused(tmp_path, monkeypatch):
    path = private_file(tmp_path)
    monkeypatch.setattr(profiles, "_profile_worker", changed_worker)
    with pytest.raises(ValueError, match="input rejected"):
        profiles.load_profile(SOURCE, str(path), timeout=30)


def malformed_worker(connection, *args):
    from network import worker
    original = worker.send
    mutation = json.loads(os.environ["OPSKIT_PROFILE_MUTATION"])
    def send(connection, result):
        if ("page" in result) == mutation['page']:result.update(mutation['value'])
        original(connection, result)
    worker.send = send
    profiles._profile_worker(connection, *args)


@pytest.mark.parametrize("mutation", [
    {"page": True, "value": {"request": "wrong"}},
    {"page": True, "value": {"page": True}},
    {"page": True, "value": {"data": "not base64"}},
    {"page": True, "value": {"data": base64.b64encode(b'x'*16385).decode()}},
    {"page": False, "value": {"bytes": True}},
    {"page": False, "value": {"sha256": "wrong"}},
])
def test_malformed_private_transfer_rejected(tmp_path, monkeypatch, mutation):
    path = private_file(tmp_path)
    monkeypatch.setenv("OPSKIT_PROFILE_MUTATION", json.dumps(mutation));monkeypatch.setattr(profiles, "_profile_worker", malformed_worker)
    with pytest.raises(RuntimeError, match="invalid fetch profile response"):
        profiles.load_profile(SOURCE, str(path), timeout=30)
