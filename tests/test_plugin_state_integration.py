"""Real publication and invocation paths must consume the ownership journal."""
import base64
from contextlib import nullcontext
import io
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_publish as publication
from state_publication_fixture import publication_worker


def payload():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("value")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"new"))
    return base64.b64encode(output.getvalue()).decode()


@pytest.fixture
def old_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    from core.plugin_trust import grant
    grant("provider", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("provider", "a" * 64),), raising=False)
    root = tmp_path / "plugin-data/provider/self"
    root.mkdir(parents=True)
    (root / "value").write_bytes(b"old")
    return root


def test_publish_error_retains_candidate_until_locked_recovery(old_state, monkeypatch):
    root = old_state
    worker = publication._publish_worker
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MODE", "fail_candidate")
    monkeypatch.setattr(publication, "_publish_worker", publication_worker)
    with sandbox._state_lock("provider"):
        with pytest.raises(publication.PublicationUncertainError, match="outcome unconfirmed"):
            sandbox._save_state(payload(), root, plugin="provider", caller="platform")
    record = state.read_record(root, plugin="provider", caller="platform")
    candidate = root.parent / record.staging / "new"
    assert (candidate / "value").read_bytes() == b"new"
    assert not root.exists()
    monkeypatch.setattr(publication, "_publish_worker", worker)
    with sandbox._state_lock("provider"):
        sandbox._recover_state(root, plugin="provider", caller="platform")
    assert (root / "value").read_bytes() == b"old"
    assert not state.record_path(root).exists()
    assert not candidate.parent.exists()


def denied_cleanup_worker(connection, *args):
    from core.plugin_state_io import _finish_worker
    def denied(*args):
        raise PermissionError("injected cleanup failure")
    state._remove_owned_tree = denied
    _finish_worker(connection, *args)


def test_postcommit_cleanup_error_keeps_new_state(old_state, monkeypatch):
    root = old_state
    from core import plugin_state_io
    worker = plugin_state_io._finish_worker
    monkeypatch.setattr(plugin_state_io, "_finish_worker", denied_cleanup_worker)
    with sandbox._state_lock("provider"):
        with pytest.raises(sandbox.SandboxError, match="state committed"):
            sandbox._save_state(payload(), root, plugin="provider", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    assert (root.with_name("self.previous") / "value").read_bytes() == b"old"
    monkeypatch.setattr(plugin_state_io, "_finish_worker", worker)
    with sandbox._state_lock("provider"):
        sandbox._recover_state(root, plugin="provider", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    assert not state.record_path(root).exists()


def denied_preparation_worker(connection, *args):
    from core import secrets
    from core.plugin_state_prepare import _prepare_worker
    def denied(path):
        raise PermissionError("injected permission preparation failure")
    secrets.restrict_permissions = denied
    _prepare_worker(connection, *args)


def test_permission_preparation_failure_cannot_publish(old_state, monkeypatch):
    root = old_state
    monkeypatch.setattr("core.plugin_state_prepare._prepare_worker", denied_preparation_worker)
    with pytest.raises(OSError, match="filesystem operation failed"):
        sandbox._save_state(payload(), root, plugin="provider", caller="platform")
    assert (root / "value").read_bytes() == b"old"
    assert not state.record_path(root).exists()
    assert not list(root.parent.glob(".state-*"))


@pytest.mark.parametrize("phase,expected", [("old_moved", b"old"), ("published", b"new")])
def test_real_save_state_process_crash_recovers(old_state, phase, expected, monkeypatch):
    root = old_state
    marker = root.parent / "crashed-writer"
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MODE", "crash_old" if phase == "old_moved" else "crash_new")
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MARKER", str(marker))
    monkeypatch.setattr(publication, "_publish_worker", publication_worker)
    with sandbox._state_lock("provider"):
        with pytest.raises(publication.PublicationUncertainError, match="outcome unconfirmed"):
            sandbox._save_state(payload(), root, plugin="provider", caller="platform")
    import psutil
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    with sandbox._state_lock("provider"):
        sandbox._recover_state(root, plugin="provider", caller="platform")
    assert (root / "value").read_bytes() == expected
    assert not state.record_path(root).exists()
    assert not list(root.parent.glob(".state-*"))


@pytest.mark.parametrize("action,caller", [("cli", "platform"), ("service.call", "caller_a")])
@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("full_staging", [False, True])
def test_invoke_recovers_partition_under_process_lock_before_resources(tmp_path, monkeypatch, action, caller, published, full_staging):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    manifest = SimpleNamespace(root=tmp_path / "code", name="provider", provides=["counter"],
                               entry="main.py", kind="cli",
                               service_contracts={"counter": {"api_version": 1, "methods": ["count"]}})
    monkeypatch.setattr(sandbox, "_read_manifest", lambda root: manifest)
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: "a" * 64)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda: None)
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    # This fixture emulates Docker; capability enforcement has dedicated tests.
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda command: None)
    monkeypatch.setattr(sandbox, "_image", lambda *args: "image")
    monkeypatch.setattr(sandbox, "_remaining", lambda *args: 30)
    monkeypatch.setattr(sandbox, "_execution_budget", lambda: nullcontext())
    for field in ("stack", "authorizations", "service_edges", "network_authorizations"):
        monkeypatch.setattr(sandbox._invocations, field, (), raising=False)
    monkeypatch.setattr("core.plugin_services.resolve_service_access", lambda *args: ())
    owner = tmp_path / "plugin-data/provider"
    root = owner / "self" if action == "cli" else owner / "callers" / caller
    root.mkdir(parents=True)
    (root / "value").write_bytes(b"old")
    candidate = root.parent / ".state-owned/new"
    candidate.mkdir(parents=True)
    (candidate / "value").write_bytes(b"new")
    state.prepare_record(root, candidate, plugin="provider", caller=caller)
    root.rename(root.with_name(root.name + ".previous"))
    if published:
        candidate.rename(root)
    retained = []
    if full_staging:
        for index in range(state.MAX_STAGING_DIRECTORIES):
            wrapper = root.parent / (".state-retained" + str(index))
            wrapper.mkdir()
            retained.append(wrapper)
    recover = sandbox._recover_state
    def observed_recovery(*args, **kwargs):
        probe = '''
from core.plugin_sandbox import _state_lock, SandboxError
try:
    with _state_lock("provider"):
        raise AssertionError("recovery did not hold process lock")
except SandboxError:
    print("busy")
'''
        result = subprocess.run([sys.executable, "-c", probe],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stdout.strip() == "busy", result.stderr
        return recover(*args, **kwargs)
    monkeypatch.setattr(sandbox, "_recover_state", observed_recovery)
    class ResourceBoundary(Exception):
        pass
    def reached_resources(*args):
        assert (root / "value").read_bytes() == (b"new" if published else b"old")
        assert not state.record_path(root).exists()
        assert not candidate.parent.exists()
        raise ResourceBoundary
    monkeypatch.setattr(sandbox, "_network", reached_resources)
    if full_staging:
        with pytest.raises(sandbox.SandboxError, match="quota check failed"):
            sandbox.invoke(manifest, action, args=[], service="counter", method="count", kwargs={}, caller=caller)
        assert (root / "value").read_bytes() == (b"new" if published else b"old")
        assert all(wrapper.is_dir() for wrapper in retained)
    else:
        with pytest.raises(ResourceBoundary):
            sandbox.invoke(manifest, action, args=[], service="counter", method="count", kwargs={}, caller=caller)
