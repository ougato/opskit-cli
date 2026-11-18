"""Service grants are explicit, content-bound and revoked independently."""
import pytest

from core import plugin_trust as trust


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    trust.grant("caller", "a" * 64, "1")
    trust.grant("provider", "b" * 64, "1")


def authorize():
    trust.grant_service("caller", "a" * 64, "storage", "provider", "b" * 64, 7, ["read"])


def allowed(method="read", provider="provider", fingerprint="b" * 64, version=7):
    return trust.is_service_authorized("caller", "a" * 64, "storage", provider, fingerprint, version, method)


def test_independent_trust_does_not_grant_rpc(identities):
    assert not allowed()
    authorize()
    assert allowed()
    assert not allowed("write")
    assert not allowed(version=2)
    assert not allowed(provider="replacement")
    assert not allowed(fingerprint="c" * 64)


@pytest.mark.parametrize("identity", ["caller", "provider"])
@pytest.mark.parametrize("mutation", ["revoke", "invalidate", "retrust"])
def test_identity_changes_do_not_inherit_rpc_grant(identities, identity, mutation):
    authorize()
    if mutation == "retrust":
        trust.grant(identity, "c" * 64, "2")
    else:
        getattr(trust, mutation)(identity)
    assert not allowed()


def test_service_revocation_keeps_independent_execution_trust(identities):
    authorize()
    trust.revoke_service("caller", "storage")
    assert not allowed()
    assert trust.is_trusted("caller", "a" * 64)
    assert trust.is_trusted("provider", "b" * 64)


def test_grant_rejects_changed_identity_fingerprint(identities):
    with pytest.raises(ValueError):
        trust.grant_service("caller", "c" * 64, "storage", "provider", "b" * 64, 7, ["read"])
    assert not allowed()


def test_retrust_same_caller_content_drops_service_grants(identities):
    authorize()
    trust.grant("caller", "a" * 64, "1")
    assert not allowed()


@pytest.mark.parametrize("mutation", ["revoke", "invalidate", "retrust"])
def test_provider_reapproval_of_same_content_does_not_restore_old_edges(identities, mutation):
    authorize()
    if mutation != "retrust":
        getattr(trust, mutation)("provider")
    trust.grant("provider", "b" * 64, "1")
    assert not allowed()
