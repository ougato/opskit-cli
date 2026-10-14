"""RPC manifest contracts preserve restrictions instead of silently discarding them."""
import pytest
import yaml

from core.plugin import load_manifest


def manifest(tmp_path, **fields):
    root = tmp_path / "owner"
    root.mkdir()
    data = {"name": "owner", "version": "1.0.0", "api_version": 2,
            "kind": "python", "entry": "owner_pkg", **fields}
    (root / "plugin.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_manifest(root)


def test_provider_business_contract_is_independent_of_sdk_version(tmp_path):
    contract = {"storage": {"api_version": 7, "methods": ["read", "open_menu"]}}
    result = manifest(tmp_path, provides=["storage"], service_contracts=contract)
    assert result is not None
    assert result.service_contracts == contract
    assert result.api_version == 2


def test_caller_restrictions_are_preserved_exactly(tmp_path):
    requirement = {"service": "storage", "source": "https://example.invalid/storage.git",
                   "provider": "storage_provider", "api_version": 7, "methods": ["read"]}
    result = manifest(tmp_path, uses=[requirement])
    assert result is not None
    assert result.uses == [requirement]


@pytest.mark.parametrize("version", [True, 0, -1, "7", 65536])
def test_invalid_business_version_is_rejected(tmp_path, version):
    assert manifest(tmp_path, provides=["storage"],
                    service_contracts={"storage": {"api_version": version, "methods": ["read"]}}) is None


@pytest.mark.parametrize("methods", [[], ["*"], ["_private"], ["read", "read"], "read", ["read"] * 65])
def test_invalid_method_set_is_rejected(tmp_path, methods):
    assert manifest(tmp_path, provides=["storage"],
                    service_contracts={"storage": {"api_version": 7, "methods": methods}}) is None


def test_contract_for_unprovided_service_is_rejected(tmp_path):
    assert manifest(tmp_path, service_contracts={"storage": {"api_version": 7, "methods": ["read"]}}) is None


def test_partial_caller_restrictions_are_not_silently_dropped(tmp_path):
    assert manifest(tmp_path, uses=[{"service": "storage", "methods": ["read"]}]) is None


def test_unknown_contract_field_is_rejected(tmp_path):
    assert manifest(tmp_path, provides=["storage"], service_contracts={
        "storage": {"api_version": 7, "methods": ["read"], "allow_all": True}}) is None


def test_legacy_manifest_remains_loadable_for_independent_entry(tmp_path):
    result = manifest(tmp_path, provides=["storage"], uses=["notify"])
    assert result is not None
    assert result.uses == [{"service": "notify", "source": ""}]
