"""Unsupported Docker limits are rejected before any plugin resource work."""
import json
from types import SimpleNamespace

import pytest

from core import plugin_sandbox as sandbox


def supported():
    return {"os": "linux", "memory": True, "swap": True, "pids": True,
            "cpu_quota": True, "cpu_period": True,
            "security": ["name=seccomp,profile=default", "name=cgroupns"]}


@pytest.mark.parametrize("field", ["memory", "swap", "pids", "cpu_quota", "cpu_period"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_missing_or_nonboolean_limit_refuses_before_image_or_state(monkeypatch, tmp_path, field, value):
    capabilities = supported()
    capabilities[field] = value
    manifest = SimpleNamespace(name="demo", root=tmp_path)
    monkeypatch.setattr(sandbox._invocations, "authorizations", (), raising=False)
    monkeypatch.setattr(sandbox, "_read_manifest", lambda root: manifest)
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: "f" * 64)
    monkeypatch.setattr(sandbox, "_check_live_authorizations", lambda: None)
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    calls = []
    def control(command, *args):
        calls.append((command, args))
        return json.dumps(capabilities)
    monkeypatch.setattr(sandbox, "_control", control)
    monkeypatch.setattr(sandbox, "_image", lambda *args: pytest.fail("unsupported engine reached SDK lookup"))
    monkeypatch.setattr(sandbox, "_recover_state", lambda *args, **kwargs: pytest.fail("unsupported engine touched state"))
    with pytest.raises(sandbox.SandboxError, match="requires memory, swap"):
        sandbox._invoke(manifest, "cli", args=[], service="", method="", kwargs={}, caller="platform")
    assert len(calls) == 1 and calls[0][0] == ["docker"] and calls[0][1][:2] == ("info", "--format")


@pytest.mark.parametrize("profile", ["default", "builtin"])
def test_known_default_seccomp_profiles_are_accepted(monkeypatch, profile):
    value = supported()
    value["security"] = ["name=seccomp,profile=" + profile]
    monkeypatch.setattr(sandbox, "_control", lambda *args: json.dumps(value))
    sandbox._require_sandbox_capabilities(["docker"])


@pytest.mark.parametrize("case", ["os", "missing", "extra", "null", "list", "broken", "seccomp", "unconfined", "security_type", "security_member"])
def test_incomplete_or_malformed_capabilities_fail_closed(monkeypatch, case):
    value = supported()
    if case == "os":
        value["os"] = "windows"
    elif case == "missing":
        del value["swap"]
    elif case == "extra":
        value["private-marker"] = "must not disclose"
    elif case in ("null", "list"):
        value = None if case == "null" else []
    elif case == "seccomp":
        value["security"] = []
    elif case == "unconfined":
        value["security"] = ["name=seccomp,profile=unconfined"]
    elif case == "security_type":
        value["security"] = "name=seccomp,profile=default"
    elif case == "security_member":
        value["security"] = [False]
    monkeypatch.setattr(sandbox, "_control", lambda *args: "private-marker: [" if case == "broken" else json.dumps(value))
    with pytest.raises(sandbox.SandboxError) as failure:
        sandbox._require_sandbox_capabilities(["docker"])
    assert "private-marker" not in str(failure.value)
