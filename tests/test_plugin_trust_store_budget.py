"""Invalid authorization stores deny access without destroying recovery evidence."""
import pytest

from core import plugin_trust as trust


@pytest.mark.parametrize("payload", [
    b"private-marker: [unfinished",
    b"[]",
    b"first: &record {fingerprint: accepted}\nsecond: *record\n",
    ("first: " + "[" * 65 + "0" + "]" * 65).encode(),
    b"#" + b"x" * (1024 * 1024),
    ("first: [" + "0," * 20001 + "0]").encode(),
], ids=["malformed", "sequence-root", "alias", "deep", "oversized", "too-many-events"])
@pytest.mark.parametrize("operation", ["grant", "revoke", "invalidate", "grant_service", "revoke_service"])
def test_invalid_store_denies_and_mutations_preserve_bytes(tmp_path, payload, operation):
    path = trust._trust_file()
    path.write_bytes(payload)
    assert not trust.is_trusted("first", "accepted")
    assert not trust.is_service_authorized("first", "accepted", "svc", "second", "other", 1, "ping")
    mutations = {
        "grant": lambda: trust.grant("first", "accepted", "1"),
        "revoke": lambda: trust.revoke("first"),
        "invalidate": lambda: trust.invalidate("first"),
        "grant_service": lambda: trust.grant_service("first", "accepted", "svc", "second", "other", 1, ["ping"]),
        "revoke_service": lambda: trust.revoke_service("first", "svc"),
    }
    with pytest.raises(RuntimeError, match="trust store"):
        mutations[operation]()
    assert path.read_bytes() == payload


def test_missing_and_valid_store_preserve_normal_lifecycle(tmp_path):
    assert trust.trusted_record("first") is None
    trust.grant("first", "a" * 64, "1", branch="develop")
    assert trust.is_trusted("first", "a" * 64)
    trust.invalidate("first")
    assert not trust.is_trusted("first", "a" * 64)
    assert trust.trusted_record("first")["branch"] == "develop"
    trust.grant("first", "b" * 64, "2")
    trust.revoke("first")
    assert trust.trusted_record("first") is None


def test_trust_file_cannot_be_a_directory(tmp_path):
    trust._trust_file().mkdir()
    assert not trust.is_trusted("first", "accepted")
    with pytest.raises(RuntimeError, match="trust store"):
        trust.grant("first", "accepted", "1")
    assert trust._trust_file().is_dir()


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_trust_file_does_not_authorize_or_modify_target(tmp_path, kind):
    import os
    target = tmp_path / "private.yaml"
    payload = b"first: {fingerprint: accepted}\n"
    target.write_bytes(payload)
    try:
        if kind == "symlink":
            trust._trust_file().symlink_to(target)
        else:
            os.link(target, trust._trust_file())
    except OSError as exc:
        pytest.skip(f"link creation unavailable: {type(exc).__name__}")
    assert not trust.is_trusted("first", "accepted")
    with pytest.raises(RuntimeError, match="trust store"):
        trust.grant("first", "accepted", "1")
    assert target.read_bytes() == payload


def test_invalid_store_diagnostics_do_not_include_yaml_content(tmp_path, monkeypatch):
    trust._trust_file().write_text("private-marker: [unfinished", encoding="utf-8")
    messages = []
    monkeypatch.setattr(trust._log, "warning", lambda *args: messages.append(str(args)))
    assert not trust.is_trusted("first", "accepted")
    assert messages
    assert all("private-marker" not in message for message in messages)


def test_publication_limits_preserve_existing_authorizations(tmp_path):
    trust.grant("first", "a" * 64, "1")
    path = trust._trust_file()
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="trust store"):
        trust.grant("second", "b" * 64, "1", source="x" * (1024 * 1024))
    assert path.read_bytes() == before
    assert trust.is_trusted("first", "a" * 64)
    assert not trust.is_trusted("second", "b" * 64)
