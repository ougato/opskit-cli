import pytest

from core import plugin_services as services, plugin_sandbox as sandbox, plugin_trust as trust
from test_plugin_service_approval_cli import installed as installed, approve


def test_unapproved_edge_never_invokes_provider(installed, monkeypatch):
    monkeypatch.setattr(sandbox, "invoke", lambda *a, **k: pytest.fail("unapproved provider invoked"))
    with pytest.raises(sandbox.SandboxError, match="service.*authorized"):
        services.call_service("storage", "read", caller="caller")


def test_approved_edge_cannot_call_another_public_method(installed, monkeypatch):
    digests, _ = installed
    assert approve(digests).exit_code == 0
    monkeypatch.setattr(sandbox, "invoke", lambda *a, **k: pytest.fail("undeclared method invoked"))
    with pytest.raises(sandbox.SandboxError, match="method"):
        services.call_service("storage", "write", caller="caller")


def test_approved_method_reaches_provider_with_host_bound_caller(installed, monkeypatch):
    digests, _ = installed
    assert approve(digests).exit_code == 0
    calls = []
    monkeypatch.setattr(sandbox, "invoke", lambda *a, **k: calls.append(k) or "result")
    assert services.call_service("storage", "read", caller="caller") == "result"
    assert calls[0]["caller"] == "caller"


def test_platform_proxy_reports_actual_business_version(installed, monkeypatch):
    monkeypatch.setattr(sandbox, "invoke", lambda manifest, action, **k: {"service_api_version": 7, "methods": ["read", "write"]})
    assert services.get_service("storage").service_api_version == 7


def test_service_edge_revocation_is_seen_by_live_chain(installed, monkeypatch):
    digests, _ = installed
    assert approve(digests).exit_code == 0
    edge = ("caller", digests["caller"], "storage", "provider", digests["provider"], 7, "read")
    monkeypatch.setattr(sandbox._invocations, "service_edges", (edge,), raising=False)
    sandbox._check_live_authorizations()
    trust.revoke_service("caller", "storage")
    with pytest.raises(sandbox.SandboxError, match="service authorization revoked"):
        sandbox._check_live_authorizations()


@pytest.mark.parametrize("caught_failure", [False, True])
def test_nested_service_dependencies_survive_return_until_outer_completion(installed, monkeypatch, caught_failure):
    from types import SimpleNamespace
    edge = ("caller", "a" * 64, "storage", "provider", "b" * 64, 7, "read")
    monkeypatch.setattr(sandbox._invocations, "service_edges", (), raising=False)
    monkeypatch.setattr(sandbox._invocations, "authorizations", (), raising=False)
    monkeypatch.setattr(trust, "is_service_authorized", lambda *args: False)
    def execute(manifest, *args, **kwargs):
        if manifest.name == "provider":
            sandbox._invocations.service_edges += (edge,)
            if caught_failure:
                raise PermissionError("caught by caller")
            return 0
        try:
            sandbox.invoke(SimpleNamespace(name="provider"), "service.call")
        except PermissionError:
            assert caught_failure
        assert sandbox._invocations.service_edges == (edge,)
        with pytest.raises(sandbox.SandboxError, match="service authorization revoked"):
            sandbox._check_live_authorizations()
        return 0
    monkeypatch.setattr(sandbox, "_invoke", execute)
    sandbox.invoke(SimpleNamespace(name="caller"), "cli")
    assert sandbox._invocations.service_edges == ()
