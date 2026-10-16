"""An interrupted publication must never be mistaken for first-run state."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import plugin_sandbox as sandbox


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("phase", ["prepared", "published", "cleaned"])
def test_real_crash_at_remaining_publication_boundaries(tmp_path, existing, phase):
    """Record crash topology; this does not provide automatic recovery."""
    root, staging = tmp_path / "self", tmp_path / ".state-owned" / "new"
    staging.mkdir(parents=True)
    (staging / "value").write_bytes(b"new")
    if existing:
        root.mkdir()
        (root / "value").write_bytes(b"old")
    script = '''
import os
from pathlib import Path
import sys
from core import plugin_sandbox as sandbox
root, staging = map(Path, sys.argv[1:3])
phase = sys.argv[3]
rename = sandbox._rename_state
def crash_rename(source, target):
    if phase == "prepared":
        os._exit(73)
    rename(source, target)
    if phase == "published" and source == staging:
        os._exit(73)
sandbox._rename_state = crash_rename
if phase == "cleaned":
    unlink = Path.unlink
    def crash_unlink(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if path.name == "self.transaction.json":
            os._exit(73)
    Path.unlink = crash_unlink
from core.plugin_state import transaction_guard, prepare_record
with transaction_guard(root):
    prepare_record(root, staging, plugin="provider", caller="platform")
    sandbox._publish_state_locked(staging, root, plugin="provider", caller="platform")
if phase == "cleaned":
    # Publication and cleanup now have distinct owners. This low-level crash
    # probe retains the original journal-last boundary; worker death/retry is
    # covered separately by test_plugin_state_io.
    from core.plugin_state import finish_transaction, transaction_guard
    with transaction_guard(root):
        finish_transaction(root, plugin="provider", caller="platform", rename=sandbox._rename_state)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(staging), phase],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30,
    )
    assert result.returncode == 73, result.stderr
    backup = root.with_name(root.name + ".previous")
    if phase == "prepared":
        assert root.exists() == existing
        if existing:
            assert (root / "value").read_bytes() == b"old"
        assert (staging / "value").read_bytes() == b"new"
    else:
        assert (root / "value").read_bytes() == b"new"
        assert not staging.exists()
    unresolved = existing and phase == "published"
    assert backup.exists() == unresolved
    if unresolved:
        assert (backup / "value").read_bytes() == b"old"
        with pytest.raises(sandbox.SandboxError, match="unresolved previous state transaction"):
            sandbox._require_resolved_state(root)
    if phase != "cleaned":
        with pytest.raises(sandbox.SandboxError, match="state transaction"):
            sandbox._require_resolved_state(root)
    else:
        sandbox._require_resolved_state(root)


def test_real_crash_retains_old_state_and_rejects_empty_restart(tmp_path):
    root, staging = tmp_path / "self", tmp_path / ".state-owned" / "new"
    root.mkdir()
    staging.mkdir(parents=True)
    (root / "value").write_bytes(b"old")
    (staging / "value").write_bytes(b"new")
    script = '''
import os
from pathlib import Path
import sys
from core import plugin_sandbox as sandbox
root, staging = map(Path, sys.argv[1:])
original = sandbox._rename_state
def rename(source, target):
    original(source, target)
    if source == root:
        os._exit(73)
sandbox._rename_state = rename
from core.plugin_state import transaction_guard, prepare_record
with transaction_guard(root):
    prepare_record(root, staging, plugin="provider", caller="platform")
    sandbox._publish_state_locked(staging, root, plugin="provider", caller="platform")
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(staging)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr
    assert not root.exists()
    with pytest.raises(sandbox.SandboxError, match="unresolved previous state transaction"):
        sandbox._require_resolved_state(root)
    assert (tmp_path / "self.previous/value").read_bytes() == b"old"
    assert (staging / "value").read_bytes() == b"new"


@pytest.mark.parametrize("action,caller", [("cli", "platform"), ("service.call", "caller_a")])
def test_unresolved_partition_refused_before_network_or_container(tmp_path, monkeypatch, action, caller):
    monkeypatch.setattr(sandbox, "data_dir", lambda: tmp_path)
    manifest = SimpleNamespace(root=tmp_path / "code", name="provider", provides=["counter"],
                               service_contracts={"counter": {"api_version": 1, "methods": ["count"]}})
    monkeypatch.setattr(sandbox, "_read_manifest", lambda root: manifest)
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: "a" * 64)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda: None)
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    # This fixture emulates Docker; capability enforcement has dedicated tests.
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda command: None)
    monkeypatch.setattr(sandbox, "_image", lambda *args: "reviewed-image")
    monkeypatch.setattr(sandbox, "_remaining", lambda *args: 30)
    monkeypatch.setattr(sandbox._invocations, "authorizations", (), raising=False)
    monkeypatch.setattr(sandbox._invocations, "service_edges", (), raising=False)
    monkeypatch.setattr("core.plugin_services.resolve_service_access", lambda *args: ())
    def forbidden(*args, **kwargs):
        pytest.fail("unresolved state reached resource creation")
    monkeypatch.setattr(sandbox, "_network", forbidden)
    monkeypatch.setattr(sandbox, "_resource_scope", forbidden)
    owner = tmp_path / "plugin-data/provider"
    root = owner / "self" if action == "cli" else owner / "callers" / caller
    backup = root.with_name(root.name + ".previous")
    backup.mkdir(parents=True)
    (backup / "value").write_bytes(b"old")
    with pytest.raises(sandbox.SandboxError, match="unresolved previous state transaction"):
        sandbox._invoke(manifest, action, args=[], service="counter", method="count", kwargs={}, caller=caller)
    assert not root.exists()
    assert (backup / "value").read_bytes() == b"old"


def test_dangling_previous_link_is_not_treated_as_absent(tmp_path):
    root = tmp_path / "self"
    backup = tmp_path / "self.previous"
    try:
        backup.symlink_to(tmp_path / "absent", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")
    with pytest.raises(sandbox.SandboxError, match="unresolved previous state transaction"):
        sandbox._require_resolved_state(root)
    assert backup.is_symlink()


def test_unresolved_state_does_not_start_payload_extraction(tmp_path, monkeypatch):
    root = tmp_path / "self"
    (tmp_path / "self.previous").mkdir()
    with pytest.raises(sandbox.SandboxError, match="unresolved previous state transaction"):
        sandbox._save_state("", root, plugin="owner", caller="platform")
