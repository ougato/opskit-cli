"""Sandbox UI waits must observe deadlines/revocation without reader threads."""
import io
import sys
from types import SimpleNamespace

import pytest

from core import config, plugin_sandbox as sandbox, plugin_trust as trust, prompt


class CancelWait(RuntimeError):
    pass


def windows_input(monkeypatch, keys=(), available=None):
    keys = iter(keys)
    monkeypatch.setattr(prompt, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(prompt, "_stdin_is_tty", lambda: True)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(
        getwch=lambda: next(keys), kbhit=available or (lambda: False)))


def checkpoint_after_wait():
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise CancelWait("wait cancelled")

    return check


def test_guard_context_restores_parent_after_failure():
    outer, inner = lambda: None, lambda: None
    with prompt.guard_input(outer):
        with pytest.raises(CancelWait):
            with prompt.guard_input(inner):
                assert prompt._input_guard.get() is inner
                raise CancelWait
        assert prompt._input_guard.get() is outer
    assert prompt._input_guard.get() is None


@pytest.mark.parametrize("reader", ["_read_key", "_read_key_seq", "_read_line"])
def test_windows_silent_input_exits_through_guard(monkeypatch, reader):
    # No input is available; calling getwch would exhaust this iterator instead
    # of producing the expected cancellation, proving the read was not entered.
    windows_input(monkeypatch)
    with pytest.raises(CancelWait, match="wait cancelled"):
        with prompt.guard_input(checkpoint_after_wait()):
            getattr(prompt, reader)()
    assert prompt._input_guard.get() is None


@pytest.mark.parametrize("reader", ["_read_key_seq", "_read_line"])
def test_windows_incomplete_navigation_sequence_is_guarded(monkeypatch, reader):
    available = iter([True])
    windows_input(monkeypatch, ["\xe0"], lambda: next(available, False))
    with pytest.raises(CancelWait):
        with prompt.guard_input(checkpoint_after_wait()):
            getattr(prompt, reader)()


def test_windows_buffer_drain_cannot_swallow_guard_failure(monkeypatch):
    windows_input(monkeypatch, ["a"] * 20, lambda: True)
    with pytest.raises(CancelWait):
        with prompt.guard_input(checkpoint_after_wait()):
            prompt._drain_input()


def test_guarded_ui_rejects_redirected_input_before_read(monkeypatch):
    class Stdin(io.StringIO):
        def read(self, *args):
            pytest.fail("sandbox UI entered redirected read")

    monkeypatch.setattr(prompt, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(prompt.sys, "stdin", Stdin("unrelated input"))
    with pytest.raises(PermissionError, match="terminal"):
        with prompt.guard_input(lambda: None):
            prompt._read_key()


def test_ordinary_redirected_input_remains_available_without_guard(monkeypatch):
    monkeypatch.setattr(prompt, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(prompt.sys, "stdin", io.StringIO("k"))
    assert prompt._read_key() == "k"


@pytest.mark.parametrize("revoked", ["caller", "provider"])
def test_live_authorization_checks_entire_nested_chain(monkeypatch, revoked):
    trust.grant('caller', 'a' * 64, '1')
    trust.grant('provider', 'b' * 64, '1')
    trust.revoke(revoked)
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("caller", "a" * 64), ("provider", "b" * 64)), raising=False)
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        sandbox._check_live_authorizations()


def test_disabled_outer_caller_denies_live_provider(monkeypatch):
    trust.grant('caller', 'a' * 64, '1')
    trust.grant('provider', 'b' * 64, '1')
    config.save_config({"modules": {"caller": {"enabled": False}}})
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("caller", "a" * 64), ("provider", "b" * 64)), raising=False)
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        sandbox._check_live_authorizations()


def test_broker_ui_checkpoint_observes_deadline_before_input(monkeypatch):
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: "fingerprint")
    monkeypatch.setattr(sandbox._invocations, "deadline", 10.0, raising=False)
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: 11.0)
    monkeypatch.setattr(prompt, "pause", lambda: pytest.fail("expired UI entered"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._broker(SimpleNamespace(name="demo"), {"rpc_version": 1, "method": "pause"})


def test_broker_rechecks_revocation_while_input_waits(monkeypatch):
    trust.grant('demo', 'a' * 64, '1')
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: 'a' * 64)
    clock = [100.0]
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def waiting():
        check = prompt._input_guard.get()
        assert check is not None
        trust.revoke('demo')
        clock[0] = 102.0
        check()

    monkeypatch.setattr(prompt, "pause", waiting)
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        sandbox._broker(SimpleNamespace(name="demo"), {"rpc_version": 1, "method": "pause"})
    assert prompt._input_guard.get() is None


def test_nested_authorization_scope_restores_after_cancellation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sandbox._invocations, "authorizations", (), raising=False)

    def execute(manifest, *args, **kwargs):
        if manifest.name == "caller":
            sandbox._invocations.authorizations = (("caller", "a"),)
            with pytest.raises(CancelWait):
                sandbox.invoke(SimpleNamespace(name="provider"), "service.call")
            assert sandbox._invocations.authorizations == (("caller", "a"),)
        else:
            sandbox._invocations.authorizations += (("provider", "b"),)
            raise CancelWait

    monkeypatch.setattr(sandbox, "_invoke", execute)
    sandbox.invoke(SimpleNamespace(name="caller"), "cli")
    assert sandbox._invocations.authorizations == ()


def test_removed_service_declaration_denied_before_runtime_lookup(tmp_path, monkeypatch):
    from core.plugin import load_manifest
    root = tmp_path / "provider"
    root.mkdir()
    (root / "plugin.yaml").write_text(
        "name: provider\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: provider_pkg\n")
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: "fingerprint")
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setattr(sandbox, "is_trusted", lambda *a: True)
    monkeypatch.setattr(sandbox, "docker_command", lambda: pytest.fail("undeclared provider runtime queried"))
    with pytest.raises(sandbox.SandboxError, match="service not provided"):
        sandbox.invoke(load_manifest(root), "service.call", service="storage")


def test_revocation_during_preparation_denies_launch_with_correct_reason(tmp_path, monkeypatch):
    from core.plugin import load_manifest
    from core.plugin_trust import grant, revoke
    root = tmp_path / "provider"
    root.mkdir()
    (root / "plugin.yaml").write_text(
        "name: provider\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: provider_pkg\n")
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "load_config", lambda: {})
    grant("provider", sandbox.compute_fingerprint(root), "1.0.0")
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])

    def preparing(*args):
        revoke("provider")
        return "image"

    monkeypatch.setattr(sandbox, "_image", preparing)
    monkeypatch.setattr(sandbox, "start", lambda *a, **k: pytest.fail("revoked preparation launched client"))
    with pytest.raises(sandbox.SandboxError, match="authorization revoked during execution"):
        sandbox.invoke(load_manifest(root), "cli")
    assert not (tmp_path / "data/plugin-data/provider/self").exists()


def test_guarded_windows_unicode_line_preserves_secret_masking(monkeypatch, capsys):
    windows_input(monkeypatch, ["中", "\r"], lambda: True)
    with prompt.guard_input(lambda: None):
        assert prompt._read_line(secret=True) == "中"
    output = capsys.readouterr().out
    assert "中" not in output and "*" in output


def test_guarded_windows_complete_arrow_keeps_existing_mapping(monkeypatch):
    windows_input(monkeypatch, ["\xe0", "H"], lambda: True)
    with prompt.guard_input(lambda: None):
        assert prompt._read_key_seq() == "UP"


@pytest.mark.parametrize("reader", ["_read_key", "_read_key_seq", "_read_line"])
@pytest.mark.parametrize("control", ["\x03", "\x04"])
def test_guarded_windows_control_keys_propagate_cancellation(monkeypatch, reader, control):
    windows_input(monkeypatch, [control], lambda: True)
    with pytest.raises(KeyboardInterrupt):
        with prompt.guard_input(lambda: None):
            getattr(prompt, reader)()
    assert prompt._input_guard.get() is None


def test_guarded_pause_does_not_swallow_cancellation(monkeypatch):
    from core.cli import non_interactive
    windows_input(monkeypatch, ["\x03"], lambda: True)
    monkeypatch.setattr(prompt, "_auto_yes", False)
    monkeypatch.setattr(prompt, "_drain_input", lambda: None)
    token = non_interactive.set(False)
    try:
        with pytest.raises(KeyboardInterrupt):
            with prompt.guard_input(lambda: None):
                prompt.pause("synthetic pause")
    finally:
        non_interactive.reset(token)


def test_ordinary_control_key_is_left_to_existing_menu_policy(monkeypatch):
    windows_input(monkeypatch, ["\x03"], lambda: True)
    assert prompt._read_key() == "\x03"
