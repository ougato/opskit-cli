"""A successful worker must not publish state before owned-resource cleanup."""
import json
import sys

import pytest

from core import plugin_sandbox as sandbox
from core.plugin import load_manifest
from core.plugin_trust import compute_fingerprint, grant, invalidate
from core.process import start


@pytest.mark.parametrize("failure", ["none", "cleanup", "revocation"])
def test_state_commit_waits_for_resource_cleanup(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo\n")
    (root / "demo.py").write_text("raise RuntimeError('host must not import')")
    manifest = load_manifest(root)
    grant("demo", compute_fingerprint(root), "1.0.0")
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    monkeypatch.setattr(sandbox, "_image", lambda *args: "image")
    objects = []
    events = []
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
        assert arguments[0] == "docker"
        assert "--label" in arguments
        objects.append({"ID": "b" * 64, "Names": arguments[arguments.index("--name") + 1]})
        wire = sandbox.PREFIX + json.dumps({"rpc_version": 1, "type": "result", "state": "candidate", "value": 7})
        return start([sys.executable, "-c", "print(" + repr(wire) + ")"], **kwargs)
    monkeypatch.setattr(sandbox, "_control", control)
    monkeypatch.setattr(sandbox, "start", client)
    monkeypatch.setattr(sandbox, "_save_state", lambda *args: events.append("commit"))
    if failure == "none":
        assert sandbox.invoke(manifest, "cli") == 7
        assert events == ["cleanup", "commit"]
    else:
        with pytest.raises(sandbox.SandboxError):
            sandbox.invoke(manifest, "cli")
        assert events == ["cleanup"]
    records = list((tmp_path / "data" / "plugin-resources").glob("*.json"))
    assert bool(records) == (failure == "cleanup")
