from types import SimpleNamespace

import pytest

from core.plugin_worker import RemoteService, _service_description


def test_business_version_is_independent_of_rpc_transport():
    service = SimpleNamespace(service_api_version=7, read=lambda: "value")
    assert _service_description(service, {"api_version": 7, "methods": ["read"]}, "read") == {
        "service_api_version": 7, "methods": ["read"]}


@pytest.mark.parametrize("version", [True, 1, "7", 8])
def test_worker_rejects_actual_business_version_mismatch(version):
    service = SimpleNamespace(service_api_version=version, read=lambda: None)
    with pytest.raises(ValueError, match="version mismatch"):
        _service_description(service, {"api_version": 7, "methods": ["read"]}, "read")


def test_worker_rejects_other_public_method():
    service = SimpleNamespace(service_api_version=7, read=lambda: None, delete=lambda: None)
    with pytest.raises(PermissionError):
        _service_description(service, {"api_version": 7, "methods": ["read"]}, "delete")


def test_worker_rejects_non_callable_attribute():
    service = SimpleNamespace(service_api_version=7, read="secret")
    with pytest.raises(ValueError, match="not callable"):
        _service_description(service, {"api_version": 7, "methods": ["read"]}, "read")


def test_guest_proxy_queries_business_version_through_broker(monkeypatch):
    calls = []
    monkeypatch.setattr("core.plugin_worker.broker", lambda *args: calls.append(args) or {"service_api_version": 7})
    assert RemoteService("storage").service_api_version == 7
    assert calls == [("service.describe", "storage")]
