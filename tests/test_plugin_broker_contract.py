"""Untrusted RPC envelopes cannot select identities or host-only callbacks."""
from types import SimpleNamespace

import pytest

from core import plugin_sandbox as sandbox, plugin_services as services, plugin_trust as trust, prompt


@pytest.fixture
def caller(monkeypatch):
    trust.grant("caller", "a" * 64, "1")
    monkeypatch.setattr(sandbox, "check_authorization", lambda manifest: "a" * 64)
    monkeypatch.setattr(sandbox._invocations, "authorizations", (), raising=False)
    monkeypatch.setattr(sandbox._invocations, "service_edges", (), raising=False)
    monkeypatch.setattr(sandbox._invocations, "deadline", None, raising=False)
    return SimpleNamespace(name="caller")


@pytest.mark.parametrize("payload", [
    None, [], {"method": []}, {"method": {}}, {"method": 1},
    {"method": "pause", "caller": "platform"},
    {"method": "pause", "fingerprint": "forged"},
    {"type": "result", "method": "pause"},
    {"method": "pause", "args": {}}, {"method": "pause", "kwargs": []},
])
def test_invalid_envelope_never_enters_host_ui(caller, monkeypatch, payload):
    monkeypatch.setattr(prompt, "pause", lambda *a, **k: pytest.fail("malformed request reached UI"))
    with pytest.raises(sandbox.SandboxError, match="invalid broker"):
        sandbox._broker(caller, payload)


@pytest.mark.parametrize("method,args,kwargs", [
    ("service.call", ["storage", "read", [], {}], {"caller": "platform"}),
    ("service.call", ["storage", "read"], {}),
    ("service.call", ["storage", "read", {}, {}], {}),
    ("service.call", ["storage", "read", [], []], {}),
    ("service.describe", ["storage", "platform"], {}),
    ("service.describe", ["storage"], {"caller": "platform"}),
])
def test_invalid_service_shapes_never_enter_provider(caller, monkeypatch, method, args, kwargs):
    monkeypatch.setattr(services, "call_service", lambda *a, **k: pytest.fail("invalid provider call"))
    monkeypatch.setattr(services, "describe_service", lambda *a, **k: pytest.fail("invalid provider description"))
    with pytest.raises(sandbox.SandboxError, match="invalid service"):
        sandbox._broker(caller, dict(rpc_version=1, type="call", method=method, args=args, kwargs=kwargs))


@pytest.mark.parametrize("method,args", [
    ("service.call", ["storage", "read", ["literal argument"], {"option": 3}]),
    ("service.describe", ["storage"]),
])
def test_valid_service_request_preserves_business_arguments_and_host_identity(caller, monkeypatch, method, args):
    calls = []
    monkeypatch.setattr(services, "call_service" if method.endswith("call") else "describe_service",
                        lambda *a, **k: calls.append((a, k)) or "result")
    assert sandbox._broker(caller, dict(rpc_version=1, type="call", method=method, args=args, kwargs={})) == "result"
    assert calls == [(tuple(args), {"caller": "caller"})]


def test_host_callback_pagination_not_a_remote_capability(caller, monkeypatch):
    monkeypatch.setattr(prompt, "paged_select", lambda *a, **k: pytest.fail("host callback pagination entered"))
    with pytest.raises(sandbox.SandboxError, match="capability not granted"):
        sandbox._broker(caller, {"rpc_version": 1, "method": "paged_select"})


def test_revoked_outer_caller_cannot_dispatch_from_still_trusted_provider(caller, monkeypatch):
    trust.grant("outer", "b" * 64, "1")
    trust.revoke("outer")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("outer", "b" * 64),), raising=False)
    # The generic fixture stubs the whole authorization check; restore its
    # live-chain check here so this test can actually exercise revocation.
    def check_authorization(manifest):
        sandbox._check_live_authorizations(extra=((manifest.name, "a" * 64),))
        return "a" * 64
    monkeypatch.setattr(sandbox, "check_authorization", check_authorization)
    monkeypatch.setattr(services, "describe_service", lambda *a, **k: pytest.fail("revoked chain dispatched"))
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        sandbox._broker(caller, {"rpc_version": 1, "method": "service.describe", "args": ["storage"]})


def test_find_executable_dispatch_uses_host_identity(caller, monkeypatch):
    from core import plugin_host_tools
    monkeypatch.setattr(sandbox._invocations, "capability_authority", object(), raising=False)
    calls = []
    monkeypatch.setattr(plugin_host_tools, "find_executable", lambda *args, **kwargs: calls.append(args) or "found")
    request = {"rpc_version": 1, "method": "host.find_executable", "args": ["git.exe", ""]}
    assert sandbox._broker(caller, request) == "found"
    assert calls == [("caller", "a" * 64, "git.exe", "")]
    request["args"] = ["git.exe"]
    with pytest.raises(sandbox.SandboxError, match="invalid host capability arguments"):
        sandbox._broker(caller, request)


@pytest.mark.parametrize("terminal,noninteractive,expected", [(True, False, True), (False, False, False), (True, True, False)])
def test_host_output_header_only_clears_interactive_terminal(caller, monkeypatch, terminal, noninteractive, expected):
    from core import plugin_host_tools
    from core.cli import non_interactive
    monkeypatch.setattr(sandbox._invocations, "capability_authority", object(), raising=False)
    events = []
    monkeypatch.setattr(prompt, "console", SimpleNamespace(
        is_terminal=terminal, print=lambda: events.append("blank")))
    monkeypatch.setattr(prompt, "clear_screen", lambda: events.append("clear"))
    monkeypatch.setattr(prompt, "print_header", lambda header: events.append(header))
    def execute(*args, **kwargs):
        kwargs["output_start"](["OpsKit", "Build"])
        return "result"
    monkeypatch.setattr(plugin_host_tools, "execute", execute)
    token = non_interactive.set(noninteractive)
    try:
        result = sandbox._broker(caller, {"rpc_version": 1, "method": "host.execute",
                                          "args": ["tool", [], "project", {}]})
    finally:
        non_interactive.reset(token)
    assert result == "result"
    assert events == (["clear", ["OpsKit", "Build"], "blank"] if expected else [])


def test_deferred_output_uses_current_invocation_journal(caller, monkeypatch, capsys):
    from core import plugin_host_tools

    monkeypatch.setattr(sandbox._invocations, "capability_authority", object(), raising=False)
    monkeypatch.setattr(sandbox._invocations, "tool_output", sandbox._DeferredToolOutput(), raising=False)

    def execute(*args, **kwargs):
        kwargs["deferred_output"]("    at failed()")
        return {"returncode": 34}

    monkeypatch.setattr(plugin_host_tools, "execute", execute)
    result = sandbox._broker(caller, {"rpc_version": 1, "method": "host.execute",
                                      "args": ["tool", [], "project", {"defer_output": True}]})
    assert result["returncode"] == 34
    assert not capsys.readouterr().out
    sandbox._broker(caller, {"rpc_version": 1, "method": "host.finish_tool_output", "args": [False]})
    assert capsys.readouterr().out == "    at failed()\n"
