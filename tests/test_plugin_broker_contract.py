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
    monkeypatch.setattr(services, "describe_service", lambda *a, **k: pytest.fail("revoked chain dispatched"))
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        sandbox._broker(caller, {"rpc_version": 1, "method": "service.describe", "args": ["storage"]})
