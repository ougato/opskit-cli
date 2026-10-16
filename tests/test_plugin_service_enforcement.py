import pytest

from core import plugin_services as services, plugin_sandbox as sandbox, plugin_trust as trust
from test_plugin_service_approval_cli import installed as installed, approve


def test_unapproved_edge_never_invokes_provider(installed, monkeypatch):
    monkeypatch.setattr(sandbox, "invoke", lambda *a, **k: pytest.fail("unapproved provider invoked"))
    with pytest.raises(sandbox.SandboxError, match="service authorization revoked"):
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


def test_service_resolution_shares_one_reader_and_does_not_read_host_trust(installed, monkeypatch):
    import psutil
    from core import plugin, plugin_inspection as inspection
    digests, _ = installed
    assert approve(digests).exit_code == 0
    observed = []
    original = inspection.ContentInspector.inventory
    def inventory(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        observed.append((self.pid, sandbox._invocations.deadline, kwargs["timeout"]))
        return result
    monkeypatch.setattr(inspection.ContentInspector, "inventory", inventory)
    monkeypatch.setattr(plugin, "_list_manifests", lambda *a, **k: pytest.fail("host scanned service inventory"))
    monkeypatch.setattr(trust, "is_service_authorized", lambda *a: pytest.fail("host read service authorization"))
    def invoke(*args, **kwargs):
        assert sandbox._invocations.inspector.pid == observed[0][0]
        assert sandbox._invocations.deadline == observed[0][1]
        return "result"
    monkeypatch.setattr(sandbox, "invoke", invoke)
    assert services.call_service("storage", "read", caller="caller") == "result"
    assert len(observed) == 2
    assert observed[0][:2] == observed[1][:2]
    assert 0 < observed[1][2] <= observed[0][2] <= 30
    assert not psutil.pid_exists(observed[0][0])
    assert sandbox._invocations.inspector is None


def test_service_resolution_cannot_restart_expired_parent_budget(installed, monkeypatch):
    import time
    digests, _ = installed
    assert approve(digests).exit_code == 0
    original = services._selected_manifests
    def expired(select, value):
        result = original(select, value)
        sandbox._invocations.deadline = time.monotonic() - 1
        return result
    monkeypatch.setattr(services, "_selected_manifests", expired)
    monkeypatch.setattr(sandbox, "invoke", lambda *a, **k: pytest.fail("expired service invoked provider"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        services.call_service("storage", "read", caller="caller")
    assert sandbox._invocations.inspector is None


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
