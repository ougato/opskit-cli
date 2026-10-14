"""Execution preparation and nested RPC must consume their caller's budget."""
from types import SimpleNamespace

import pytest

from core import config, plugin_sandbox as sandbox


@pytest.fixture(autouse=True)
def injected_policy_reader(monkeypatch):
    """Clock/state tests inject the reader; actual I/O is tested separately."""
    from core.plugin_inspection import ContentInspector
    monkeypatch.setattr(ContentInspector, "execution_timeout",
                        lambda self, path, *, timeout: sandbox._sandbox_timeout(config.load_config()))


def test_nested_invocation_cannot_reset_parent_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "load_config", lambda: {"plugin": {"sandbox": {"timeout": 2}}})
    clock = [100.0]
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: clock[0])
    observed = []

    def execute(manifest, *args, **kwargs):
        observed.append(getattr(sandbox._invocations, "deadline", None))
        if manifest.name == "parent":
            clock[0] += 1.5
            sandbox.invoke(SimpleNamespace(name="child"), "service.call")

    monkeypatch.setattr(sandbox, "_invoke", execute)
    sandbox.invoke(SimpleNamespace(name="parent"), "cli")
    assert observed == [102.0, 102.0]
    assert getattr(sandbox._invocations, "deadline", None) is None


def test_preparation_expiry_does_not_launch_container(tmp_path, monkeypatch):
    from core.plugin import load_manifest
    from core.plugin_trust import grant
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text(
        "name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo_pkg\n")
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "load_config", lambda: {"plugin": {"sandbox": {"timeout": 2}}})
    clock = [100.0]
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: clock[0])
    grant("demo", sandbox.compute_fingerprint(root), "1.0.0")
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])

    def delayed_image(*args):
        clock[0] += 3
        return "image"

    monkeypatch.setattr(sandbox, "_image", delayed_image)
    monkeypatch.setattr(sandbox, "start", lambda *a, **k: pytest.fail("expired preparation launched client"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox.invoke(load_manifest(root), "cli")


def test_control_caps_timeout_at_remaining_budget(monkeypatch):
    monkeypatch.setattr(sandbox._invocations, "deadline", 12.0, raising=False)
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: 10.0)
    budgets = []

    def capture(*args, **kwargs):
        budgets.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(sandbox, "capture", capture)
    assert sandbox._control(["docker"], "inspect") == "ok"
    assert budgets == [2.0]


def test_nested_shorter_policy_restores_parent_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    timeouts = iter([2, 1])
    monkeypatch.setattr(config, "load_config", lambda: {"plugin": {"sandbox": {"timeout": next(timeouts)}}})
    clock = [100.0]
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: clock[0])
    observed = []

    def execute(manifest, *args, **kwargs):
        observed.append(sandbox._invocations.deadline)
        if manifest.name == "parent":
            clock[0] += 0.5
            sandbox.invoke(SimpleNamespace(name="child"), "service.call")
            observed.append(sandbox._invocations.deadline)

    monkeypatch.setattr(sandbox, "_invoke", execute)
    sandbox.invoke(SimpleNamespace(name="parent"), "cli")
    assert observed == [102.0, 101.5, 102.0]
    assert sandbox._invocations.deadline is None


@pytest.mark.parametrize("timeout", [True, 0, -1, 3601, "bad", float("nan"), float("inf")])
def test_invalid_policy_cannot_start_execution(tmp_path, monkeypatch, timeout):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "load_config", lambda: {"plugin": {"sandbox": {"timeout": timeout}}})
    monkeypatch.setattr(sandbox, "_invoke", lambda *a, **k: pytest.fail("invalid policy executed"))
    with pytest.raises(sandbox.SandboxError, match="invalid sandbox execution timeout"):
        sandbox.invoke(SimpleNamespace(name="demo"), "cli")
    assert getattr(sandbox._invocations, "deadline", None) is None


def test_expired_budget_denies_control_but_owned_cleanup_still_runs(monkeypatch):
    monkeypatch.setattr(sandbox._invocations, "deadline", 9.0, raising=False)
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: 10.0)
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(sandbox, "capture", capture)
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._control(["docker"], "run")
    assert calls == []
    sandbox._cleanup_control(["docker"], "rm", "-f", "owned")
    assert calls == [(["docker", "rm", "-f", "owned"], sandbox.CONTROL_TIMEOUT)]
    assert sandbox._invocations.deadline == 9.0


def test_late_control_success_does_not_extend_execution(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(sandbox._invocations, "deadline", 12.0, raising=False)
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: clock[0])

    def capture(*args, **kwargs):
        clock[0] = 12.1
        return SimpleNamespace(returncode=0, stdout="late")

    monkeypatch.setattr(sandbox, "capture", capture)
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._control(["docker"], "inspect")


def test_budget_restored_after_cancel_and_next_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    calls = []

    def execute(*args, **kwargs):
        calls.append(sandbox._invocations.deadline)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return "ok"

    monkeypatch.setattr(sandbox, "_invoke", execute)
    with pytest.raises(KeyboardInterrupt):
        sandbox.invoke(SimpleNamespace(name="demo"), "cli")
    assert sandbox._invocations.stack == []
    assert sandbox._invocations.deadline is None
    assert sandbox.invoke(SimpleNamespace(name="demo"), "cli") == "ok"


def test_real_silent_control_uses_inherited_short_budget(monkeypatch):
    import sys
    import time
    monkeypatch.setattr(sandbox._invocations, "deadline", time.monotonic() + 0.2, raising=False)
    started = time.monotonic()
    with pytest.raises(sandbox.SandboxError):
        sandbox._control([sys.executable, "-c", "import time; time.sleep(30)"])
    assert time.monotonic() - started < 5


def test_state_lock_wait_is_inside_execution_budget(tmp_path, monkeypatch):
    import threading
    import time
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "load_config", lambda: {"plugin": {"sandbox": {"timeout": 1}}})
    monkeypatch.setattr(sandbox, "_invoke", lambda *a, **k: pytest.fail("busy plugin executed"))
    lock = threading.RLock()
    ready, release = threading.Event(), threading.Event()

    def owner():
        with lock:
            ready.set()
            release.wait(5)

    monkeypatch.setitem(sandbox._locks, "busy_budget", lock)
    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert ready.wait(2)
        started = time.monotonic()
        with pytest.raises(sandbox.SandboxError, match="deadline waiting for state lock"):
            sandbox.invoke(SimpleNamespace(name="busy_budget"), "cli")
        assert time.monotonic() - started < 3
        assert getattr(sandbox._invocations, "deadline", None) is None
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()


def test_config_loading_also_consumes_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: clock[0])

    def delayed_config():
        clock[0] += 3
        return {"plugin": {"sandbox": {"timeout": 2}}}

    monkeypatch.setattr(config, "load_config", delayed_config)
    monkeypatch.setattr(sandbox, "_invoke", lambda *a, **k: pytest.fail("expired config executed"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox.invoke(SimpleNamespace(name="demo"), "cli")
    assert sandbox._invocations.deadline is None
