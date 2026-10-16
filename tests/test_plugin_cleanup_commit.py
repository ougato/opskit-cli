"""A successful worker must not publish state before owned-resource cleanup."""
import json
import sys
import time
from contextlib import contextmanager

import pytest

from core import plugin_sandbox as sandbox
from core.plugin import load_manifest
from core.plugin_trust import compute_fingerprint, grant, invalidate
from core.process import start


@pytest.mark.parametrize("failure", ["none", "cleanup", "revocation", "deadline"])
@pytest.mark.parametrize("worker_failure", [False, True])
def test_state_commit_waits_for_resource_cleanup(tmp_path, monkeypatch, failure, worker_failure):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo\n")
    (root / "demo.py").write_text("raise RuntimeError('host must not import')")
    manifest = load_manifest(root)
    grant("demo", compute_fingerprint(root), "1.0.0")
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    # This fixture emulates Docker; capability enforcement has dedicated tests.
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda command: None)
    monkeypatch.setattr(sandbox, "_image", lambda *args: "image")
    if failure == "deadline":
        original_scope = sandbox._resource_scope

        @contextmanager
        def expire_after_cleanup(*args, **kwargs):
            with original_scope(*args, **kwargs) as lease:
                yield lease
            # Expire only after the real resource scope finishes. No sleeps,
            # relaxed cleanup assertions or dependency on machine load.
            sandbox._invocations.deadline = time.monotonic() - 1

        monkeypatch.setattr(sandbox, "_resource_scope", expire_after_cleanup)
    objects = []
    events = []
    workspaces = []
    def control(command, *args):
        if args[0] == "info":
            return "daemon-a"
        if args[:2] == ("container", "ls"):
            return "\n".join(json.dumps(obj) for obj in objects)
        if args[:2] == ("rm", "-f"):
            events.append("cleanup")
            if failure == "cleanup":
                raise sandbox.SandboxError("cleanup failed")
            objects.clear()
            if failure == "revocation":
                invalidate("demo")
            return ""
        pytest.fail("unexpected control command")
    def client(arguments, **kwargs):
        from pathlib import Path
        request_mount = next(value for value in arguments
                             if isinstance(value, str) and value.endswith(",dst=/request.json,readonly"))
        workspaces.append(Path(request_mount.split("src=", 1)[1].split(",dst=", 1)[0]).parent)
        assert arguments[0] == "docker"
        assert "--label" in arguments
        objects.append({"ID": "b" * 64, "Names": arguments[arguments.index("--name") + 1]})
        wire = sandbox.PREFIX + json.dumps({"rpc_version": 1, "type": "result", "state": "candidate", "value": 7})
        if worker_failure:
            wire = sandbox.PREFIX + json.dumps({"type": "invalid"})
        return start([sys.executable, "-c", "print(" + repr(wire) + ")"], **kwargs)
    monkeypatch.setattr(sandbox, "_control", control)
    monkeypatch.setattr(sandbox, "start", client)
    monkeypatch.setattr(sandbox, "_save_state", lambda *args, **kwargs: events.append("commit"))
    if failure == "none" and not worker_failure:
        assert sandbox.invoke(manifest, "cli") == 7
        assert events == ["cleanup", "commit"]
    else:
        expected = "execution deadline" if failure == "deadline" and not worker_failure else None
        with pytest.raises(sandbox.SandboxError, match=expected):
            sandbox.invoke(manifest, "cli")
        assert events == ["cleanup"]
    records = list((tmp_path / "data" / "plugin-resources").glob("*.json"))
    assert bool(records) == (failure == "cleanup")
    assert len(workspaces) == 1
    assert workspaces[0].exists() == (failure == "cleanup")
    if failure == "cleanup":
        assert (workspaces[0] / "request.json").is_file()
        assert (workspaces[0] / "plugin" / "demo.py").is_file()
