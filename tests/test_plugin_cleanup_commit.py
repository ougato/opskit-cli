"""Native worker success cannot publish state before cleanup and revalidation."""
import time
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("prepared_native_catalog")

from core import plugin_sandbox as sandbox
from core.plugin import load_manifest
from core.plugin_trust import compute_fingerprint, grant, invalidate


@pytest.mark.parametrize("failure", ["none", "cleanup", "revocation", "deadline"])
@pytest.mark.parametrize("worker_failure", [False, True])
def test_state_commit_waits_for_resource_cleanup(tmp_path, monkeypatch, failure, worker_failure):
    from core import native_invocation, plugin_workspace
    from core.native_runtime_catalog import RuntimeCatalog
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo\n")
    (root / "demo.py").write_text("raise RuntimeError('host must not import')")
    manifest = load_manifest(root)
    grant("demo", compute_fingerprint(root), "1.0.0")
    monkeypatch.setattr(RuntimeCatalog, "load", lambda *a, **k: (tmp_path / "runtime", {}))
    monkeypatch.setattr(RuntimeCatalog, "acquire", lambda *a, **k: nullcontext((tmp_path / "runtime", {})))
    monkeypatch.setattr(native_invocation, "prepare_runtime", lambda *a, **k: nullcontext(lambda: None))
    events = []
    @contextmanager
    def workspace(**kwargs):
        base = tmp_path / "workspace"
        base.mkdir()
        try:
            yield SimpleNamespace(root=base)
        finally:
            events.append("workspace_cleanup")
            if failure == "cleanup":
                raise sandbox.SandboxError("cleanup failed")
            if failure == "revocation":
                invalidate("demo")
            if failure == "deadline":
                sandbox._invocations.deadline = time.monotonic() - 1
    monkeypatch.setattr(plugin_workspace, "owned_workspace", workspace)
    def worker(*args, **kwargs):
        try:
            kwargs["checkpoint"]()
            if worker_failure:
                raise sandbox.SandboxError("worker failed")
            return {"state": "candidate", "value": 7}
        finally:
            events.append("worker_cleanup")
    monkeypatch.setattr(native_invocation, "invoke", worker)
    monkeypatch.setattr(sandbox, "_save_state", lambda *a, **k: events.append("commit"))
    if failure == "none" and not worker_failure:
        assert sandbox.invoke(manifest, "cli") == 7
        assert events == ["worker_cleanup", "workspace_cleanup", "commit"]
    else:
        with pytest.raises(sandbox.SandboxError):
            sandbox.invoke(manifest, "cli")
        assert events == ["worker_cleanup", "workspace_cleanup"]
    assert not (tmp_path / "data/plugin-resources").exists()
    assert getattr(sandbox._invocations, "deadline", None) is None
