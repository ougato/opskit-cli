"""Official service approval binds verified manifests without importing plugins."""
import json

import pytest
import yaml
from typer.testing import CliRunner

from core.cli import configure
from core import plugin_trust as trust


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    digests = {}
    for name in ("caller", "provider"):
        root = tmp_path / "plugins" / name
        root.mkdir(parents=True)
        (root / (name + "_pkg")).mkdir()
        (root / (name + "_pkg") / "__init__.py").write_text("raise RuntimeError('must never import during approval')\n")
        data = {"name": name, "version": "1.0.0", "api_version": 2, "kind": "python", "entry": name + "_pkg"}
        if name == "caller":
            data["uses"] = [{"service": "storage", "provider": "provider", "api_version": 7, "methods": ["read"]}]
        else:
            data["provides"] = ["storage"]
            data["service_contracts"] = {"storage": {"api_version": 7, "methods": ["read", "write"]}}
        (root / "plugin.yaml").write_text(yaml.safe_dump(data))
        digests[name] = trust.compute_fingerprint(root)
        trust.grant(name, digests[name], "1")
    yield digests, tmp_path
    configure("text", False)


def call(*args):
    import main
    return CliRunner().invoke(main.app, ["--output", "json", "plugin", *args])


def approve(digests, caller_digest=None):
    return call("authorize-service", "caller", "storage", "--caller-fingerprint", caller_digest or digests["caller"],
                "--provider-fingerprint", digests["provider"])


def test_explicit_service_approval_and_revocation(installed):
    digests, _ = installed
    result = approve(digests)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["methods"] == ["read"]
    assert trust.is_service_authorized("caller", digests["caller"], "storage", "provider", digests["provider"], 7, "read")
    assert not trust.is_service_authorized("caller", digests["caller"], "storage", "provider", digests["provider"], 7, "write")
    assert call("revoke-service", "caller", "storage").exit_code == 0
    assert not trust.is_service_authorized("caller", digests["caller"], "storage", "provider", digests["provider"], 7, "read")


def test_wrong_fingerprint_cannot_approve(installed):
    digests, _ = installed
    result = approve(digests, "0" * 64)
    assert result.exit_code == 2
    assert "service_grants" not in trust.trusted_record("caller")


def test_disabled_provider_cannot_be_approved(installed):
    from plugin.commands import set_enabled
    digests, _ = installed
    set_enabled("provider", False)
    result = approve(digests)
    assert result.exit_code == 1
    assert "service_grants" not in trust.trusted_record("caller")


def test_show_exposes_reviewable_contract_without_importing_code(installed):
    result = call("show", "caller")
    assert result.exit_code == 0, result.output
    requirement = json.loads(result.stdout)["data"]["service_requirements"][0]
    assert requirement == {"service": "storage", "provider": "provider", "api_version": 7, "methods": ["read"]}
    assert "source" not in requirement


@pytest.mark.parametrize("change", ["version", "methods", "provider"])
def test_mismatched_contract_cannot_be_approved(installed, change):
    digests, directory = installed
    path = directory / "plugins/caller/plugin.yaml"
    data = yaml.safe_load(path.read_text())
    data["uses"][0][{"version": "api_version", "methods": "methods", "provider": "provider"}[change]] = {
        "version": 8, "methods": ["delete"], "provider": "replacement"}[change]
    path.write_text(yaml.safe_dump(data))
    digests["caller"] = trust.compute_fingerprint(path.parent)
    trust.grant("caller", digests["caller"], "1")
    result = approve(digests)
    assert result.exit_code == 2, result.output
    assert "service_grants" not in trust.trusted_record("caller")
