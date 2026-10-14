"""Real stores and owned readers: fresh revocation, locks and blocked I/O."""
import json
import os
from pathlib import Path
import time

import psutil
import pytest

from core import config, plugin_inspection as inspection, plugin_sandbox as sandbox, plugin_trust as trust


def _trusted_manifest(tmp_path):
    from types import SimpleNamespace
    root = tmp_path / "demo"
    root.mkdir()
    (root / "demo.py").write_text("raise RuntimeError('never execute plugin in host')\n")
    fingerprint = trust.compute_fingerprint(root)
    trust.grant("demo", fingerprint, "1")
    return SimpleNamespace(name="demo", root=root, permissions=[]), fingerprint


def test_standalone_check_authorization_uses_owned_reader_and_reaps(tmp_path, monkeypatch):
    manifest, fingerprint = _trusted_manifest(tmp_path)
    pids = []
    original = inspection.ContentInspector.authorize
    def observed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        pids.append(self.pid)
        return result
    monkeypatch.setattr(inspection.ContentInspector, "authorize", observed)
    monkeypatch.setattr(config, "load_config", lambda: pytest.fail("synchronous host config read"))
    monkeypatch.setattr(sandbox, "is_trusted", lambda *args: pytest.fail("synchronous host trust read"))
    assert sandbox.check_authorization(manifest) == fingerprint
    assert pids and not psutil.pid_exists(pids[0])
    assert sandbox._invocations.inspector is None
    assert sandbox._invocations.deadline is None


def test_disabled_standalone_check_denies_before_content(tmp_path, monkeypatch):
    manifest, _ = _trusted_manifest(tmp_path)
    config.save_config({"modules": {"demo": {"enabled": False}}})
    monkeypatch.setattr(sandbox, "_inspect_content", lambda *args, **kwargs: pytest.fail("disabled content read"))
    with pytest.raises(sandbox.SandboxError, match="plugin is disabled"):
        sandbox.check_authorization(manifest)
    assert sandbox._invocations.inspector is None


def test_standalone_check_reloads_revocation_and_preserves_root_refusal(tmp_path):
    manifest, _ = _trusted_manifest(tmp_path)
    with sandbox._execution_budget():
        pid = sandbox._invocations.inspector.pid
        sandbox.check_authorization(manifest)
        manifest.permissions = ["root"]
        with pytest.raises(sandbox.SandboxError, match="cannot request host root"):
            sandbox.check_authorization(manifest)
        manifest.permissions = []
        trust.revoke("demo")
        with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
            sandbox.check_authorization(manifest)
        assert sandbox._invocations.inspector.pid == pid
    assert not psutil.pid_exists(pid)


def _blocked_initial_check(connection, buffer, length, requested):
    from network.worker import send
    while True:
        requested.wait()
        requested.clear()
        request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
        if request.get("operation") == "timeout":
            result = inspection._timeout_result(request)
        elif request.get("operation") == "setting":
            result = inspection._setting_result(request)
        elif request.get("operation") == "authorize":
            Path(os.environ["OPSKIT_TEST_AUTHORIZATION_MARKER"]).write_text(str(os.getpid()))
            read, write = os.pipe()
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
                connection.close()
            return
        else:
            result = inspection._inspect_result(request["source"], request["verify"])
        send(connection, result)


@pytest.mark.parametrize("cancel", [False, True], ids=["deadline", "cancel"])
def test_standalone_initial_authorization_block_reaped(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from types import SimpleNamespace
    from network import worker
    manifest, _ = _trusted_manifest(tmp_path)
    config.save_config({"plugin": {"sandbox": {"timeout": 30}}})
    original_config = config.get_config_path().read_bytes()
    marker = tmp_path / "authorization-pid"
    monkeypatch.setenv("OPSKIT_TEST_AUTHORIZATION_MARKER", str(marker))
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_initial_check)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker, startup_budget=30)
    expected = KeyboardInterrupt if cancel else sandbox.SandboxError
    with pytest.raises(expected):
        sandbox.check_authorization(manifest)
    assert not psutil.pid_exists(int(marker.read_text()))
    assert config.get_config_path().read_bytes() == original_config
    assert sandbox._invocations.deadline is None
    assert sandbox._invocations.inspector is None


def _approve():
    trust.grant("caller", "a" * 64, "1")
    trust.grant("provider", "b" * 64, "1")
    trust.grant_service("caller", "a" * 64, "storage", "provider", "b" * 64, 7, ["read"])
    return (("caller", "a" * 64), ("provider", "b" * 64)), (
        ("caller", "a" * 64, "storage", "provider", "b" * 64, 7, "read"),)


def _blocked_authorization(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    read, write = os.pipe()
    Path(request["config"]).write_text(str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def test_reader_reuses_pid_but_rereads_real_service_revocation(tmp_path):
    identities, edges = _approve()
    reader = inspection.ContentInspector()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "code.py").write_text("pass\n")
        reader.inspect(source, verify_checksums=False, timeout=30)
        content_pid = reader.pid
        assert reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30) is None
        pid = reader.pid
        assert pid == content_pid
        trust.revoke_service("caller", "storage")
        assert reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30) == "service"
        assert reader.pid == pid
        trust.revoke("caller")
        assert reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30) == "identity"
    finally:
        reader.close()
    assert not psutil.pid_exists(pid)


def test_locked_parent_can_read_and_later_observes_disabled_config(tmp_path):
    identities, edges = _approve()
    reader = inspection.ContentInspector()
    try:
        with config.configuration_transaction(), trust.authorization_transaction():
            assert reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30) is None
        config.save_config({"modules": {"provider": {"enabled": False}}})
        with config.configuration_transaction(), trust.authorization_transaction():
            assert reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30) == "identity"
    finally:
        reader.close()


def test_sandbox_scope_uses_reader_and_preserves_live_revoke(tmp_path, monkeypatch):
    identities, edges = _approve()
    monkeypatch.setattr(sandbox._invocations, "authorizations", identities, raising=False)
    monkeypatch.setattr(sandbox._invocations, "service_edges", edges, raising=False)
    with sandbox._execution_budget():
        sandbox._check_live_authorizations()
        pid = sandbox._invocations.inspector.pid
        trust.revoke_service("caller", "storage")
        with pytest.raises(sandbox.SandboxError, match="service authorization revoked"):
            sandbox._check_live_authorizations()
        assert sandbox._invocations.inspector.pid == pid
    assert not psutil.pid_exists(pid)


def test_authorization_blocked_read_is_reaped_under_parent_deadline(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "worker-pid"
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_authorization)
    timeout, observed = expire_worker_after_marker(marker)
    reader = inspection.ContentInspector()
    with pytest.raises(TimeoutError):
        reader.authorize(marker, tmp_path / "trust", (), (), timeout=timeout)
    assert observed
    assert reader.pid is None
    assert not psutil.pid_exists(int(marker.read_text()))


def test_cancel_reaps_blocked_authorization_reader(tmp_path, monkeypatch):
    from network import worker
    from types import SimpleNamespace
    marker = tmp_path / "worker-pid"
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_authorization)
    def clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    reader = inspection.ContentInspector()
    with pytest.raises(KeyboardInterrupt):
        reader.authorize(marker, tmp_path / "trust", (), (), timeout=30)
    assert reader.pid is None
    assert not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("store", ["config", "trust"])
def test_bad_real_store_never_authorizes_or_echoes_private_contents(tmp_path, store):
    identities, edges = _approve()
    path = config.get_config_path() if store == "config" else trust._trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private-marker: [not valid yaml")
    reader = inspection.ContentInspector()
    try:
        if store == "trust":
            assert reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30) == "identity"
        else:
            with pytest.raises(RuntimeError) as caught:
                reader.authorize(config.get_config_path(), trust._trust_file(), identities, edges, timeout=30)
            assert str(caught.value) == "plugin authorization inspection failed"
    finally:
        reader.close()


def test_scoped_result_cannot_outlive_shared_deadline(tmp_path, monkeypatch):
    identities, edges = _approve()
    monkeypatch.setattr(sandbox._invocations, "inspection_scope", True, raising=False)
    monkeypatch.setattr(sandbox._invocations, "deadline", time.monotonic() + 30, raising=False)
    class Reader:
        def authorize(self, *args, **kwargs):
            sandbox._invocations.deadline = time.monotonic() - 1
            return None
    monkeypatch.setattr(sandbox._invocations, "inspector", Reader(), raising=False)
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._check_live_authorizations(extra=identities)


@pytest.mark.parametrize("response", [{}, {"ok": 1, "failure": None},
                                     {"ok": True, "failure": "unknown"},
                                     {"ok": True, "failure": None, "records": {}}])
def test_authorization_response_has_fixed_shape(response):
    with pytest.raises(RuntimeError, match="authorization inspection failed"):
        inspection._authorization_failure(response)


def test_oversized_authorization_request_never_starts_reader(tmp_path):
    reader = inspection.ContentInspector()
    with pytest.raises(ValueError, match="invalid plugin authorization"):
        reader.authorize(tmp_path, tmp_path, [("caller", "a" * 64)] * 257, (), timeout=30)
    assert reader.pid is None
    reader.close()


def _blocked_live_authorization(connection, buffer, length, requested):
    """Complete startup settings, then block only the actual live check."""
    from network.worker import send
    while requested.wait():
        requested.clear()
        request = json.loads(bytes(memoryview(buffer).cast('B')[:length.value]))
        if request.get('operation') == 'timeout':
            send(connection, {'ok': True, 'timeout': 30})
            continue
        Path(request['config']).write_text(str(os.getpid()))
        read, write = os.pipe()
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
            connection.close()
        return


def test_standalone_live_check_uses_reader_reloads_and_reaps(monkeypatch):
    identities, edges = _approve()
    monkeypatch.setattr(sandbox._invocations, 'authorizations', identities, raising=False)
    monkeypatch.setattr(sandbox._invocations, 'service_edges', edges, raising=False)
    original_load = trust._load
    monkeypatch.setattr(config, 'load_config', lambda: pytest.fail('host synchronous config read'))
    monkeypatch.setattr(trust, '_load', lambda: pytest.fail('host synchronous trust read'))
    original = inspection.ContentInspector.authorize
    pids = []
    def observed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        pids.append(self.pid)
        return result
    monkeypatch.setattr(inspection.ContentInspector, 'authorize', observed)
    sandbox._check_live_authorizations()
    # Restore only the parent store helper so the real revocation can be saved.
    monkeypatch.setattr(trust, '_load', original_load)
    trust.revoke_service('caller', 'storage')
    monkeypatch.setattr(sandbox._invocations, 'authorizations', identities, raising=False)
    monkeypatch.setattr(sandbox._invocations, 'service_edges', edges, raising=False)
    with pytest.raises(sandbox.SandboxError, match='service authorization revoked'):
        sandbox._check_live_authorizations()
    assert pids and not any(psutil.pid_exists(pid) for pid in pids)
    assert sandbox._invocations.inspector is None
    assert sandbox._invocations.deadline is None


def test_standalone_blocked_live_check_is_reaped(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / 'live-reader-pid'
    monkeypatch.setattr(config, 'get_config_path', lambda: marker)
    monkeypatch.setattr(inspection, '_inspection_session', _blocked_live_authorization)
    _, observed = expire_worker_after_marker(marker)
    with pytest.raises(sandbox.SandboxError, match='execution deadline during authorization'):
        sandbox._check_live_authorizations()
    assert observed and not psutil.pid_exists(int(marker.read_text()))
    assert sandbox._invocations.inspector is None
    assert sandbox._invocations.deadline is None


def test_standalone_cancelled_live_check_is_reaped(tmp_path, monkeypatch):
    from network import worker
    from types import SimpleNamespace
    marker = tmp_path / 'live-reader-pid'
    monkeypatch.setattr(config, 'get_config_path', lambda: marker)
    monkeypatch.setattr(inspection, '_inspection_session', _blocked_live_authorization)
    def clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    with pytest.raises(KeyboardInterrupt):
        sandbox._check_live_authorizations()
    assert not psutil.pid_exists(int(marker.read_text()))
    assert sandbox._invocations.inspector is None
    assert sandbox._invocations.deadline is None
