"""Credentials authorize one complete source, never merely its hostname."""
import base64
from dataclasses import replace
import json
import socket
import struct

import pytest

from core.plugin_fetch_source import remote_source, authentication


def profile(source, **auth):
    return {"schema_version": 1, "source": source, "authentication": auth}


def host_key(body=b"x" * 32):
    algorithm = b"ssh-ed25519"
    blob = struct.pack(">I", len(algorithm)) + algorithm + struct.pack(">I", len(body)) + body
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nQUJD\n-----END OPENSSH PRIVATE KEY-----\n"


def test_canonical_https_scope_and_destination_without_dns(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("source policy attempted DNS")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    first = remote_source("https://EXAMPLE.com.:443/org/%72epo.git/")
    second = remote_source("https://example.com/org/repo.git")
    assert first == second and first.identity == second.identity
    assert first.url == "https://example.com:443/org/repo.git"
    assert first.authority == "example.com:443"
    assert authentication(first).payload() == {"kind": "anonymous", "source_id": first.identity}


def test_unicode_spaces_and_literal_percent_are_stable():
    value = remote_source("https://example.com/%E7%8C%AB/a%20b%25.git")
    assert value.path == "/猫/a b%.git" and remote_source(value.url) == value


def test_scp_relative_and_absolute_ssh_paths_are_distinct():
    scp = remote_source("git@EXAMPLE.com:org/repo.git")
    absolute = remote_source("ssh://git@example.com/org/repo.git")
    assert scp.relative and not absolute.relative and scp.identity != absolute.identity
    assert scp.url == "git@example.com:org/repo.git" and remote_source(scp.url) == scp
    assert remote_source("git@example.com:/org/repo.git") == absolute


def test_ipv6_and_explicit_ssh_port():
    value = remote_source("ssh://git@[2001:db8::1]:2222/org/repo.git")
    assert value.host == "2001:db8::1" and value.authority == "[2001:db8::1]:2222"
    assert remote_source(value.url) == value


@pytest.mark.parametrize("value", ["http://example.com/a", "git://example.com/a", "file:///tmp/a",
                                   "https://user:synthetic-secret@example.com/a", "https://user@example.com/a",
                                   "https://example.com/a?token=synthetic-secret", "https://example.com/a#ref",
                                   "https://example.com/a?", "https://example.com/a#", "https://example.com",
                                   "https://example.com/a/../b", "https://example.com/%2e%2e/a",
                                   "https://example.com/a%2fb", "https://example.com/a%5cb",
                                   "https://example.com/%00", "https://example.com/%GG",
                                   "https://example.com/a//b", "https://example.com:0/a", "https://example.com:65536/a",
                                   " https://example.com/a", "https://example.com/a\n", "https://example.com/a\\b",
                                   "ssh://example.com/a", "ssh://-option@example.com/a", "git@example.com:../a",
                                   "git@example.com:a//b", "git@example.com:-option", "example.com:repo", "-helper::payload"])
def test_ambiguous_insecure_or_credential_bearing_sources_rejected_without_echo(value):
    with pytest.raises(ValueError) as caught:
        remote_source(value)
    assert str(caught.value) == "invalid remote fetch source"


@pytest.mark.parametrize("other", ["https://other.invalid/org/repo.git", "https://example.com:444/org/repo.git",
                                   "https://example.com/org/other.git", "https://example.com/ORG/repo.git",
                                   "https://example.com/org/repo.git/nested"])
def test_same_host_or_path_prefix_cannot_expand_credential_scope(other):
    source = remote_source("https://example.com/org/repo.git")
    with pytest.raises(ValueError, match="source binding"):
        authentication(source, profile(other, kind="bearer", token="synthetic-token"))


def test_bearer_and_basic_material_is_scoped_and_not_in_repr():
    url = "https://example.com/org/repo.git";source = remote_source(url)
    bearer = authentication(source, profile(url, kind="bearer", token="synthetic-token"))
    assert bearer.payload()["authorization"] == "Bearer synthetic-token"
    assert "synthetic-token" not in repr(bearer)
    basic = authentication(source, profile(url, kind="basic", username="demo", password="synthetic-password"))
    assert base64.b64decode(basic.payload()["authorization"].split()[1]) == b"demo:synthetic-password"
    assert "synthetic-password" not in repr(basic)
    changed = bearer.payload();changed["authorization"] = "changed"
    assert bearer.payload()["authorization"] == "Bearer synthetic-token"


@pytest.mark.parametrize("auth", [{"kind": "bearer", "token": "synthetic-secret\r\nInjected:x"},
                                  {"kind": "basic", "username": "bad:user", "password": "synthetic-secret"},
                                  {"kind": "basic", "username": "user", "password": "x\n"},
                                  {"kind": "bearer", "token": "x", "helper": "arbitrary-program"},
                                  {"kind": "anonymous", "verify_tls": False},
                                  {"kind": "bearer", "token": "=x"},
                                  {"kind": "agent"}])
def test_invalid_authentication_does_not_echo_secret_or_allow_helper(auth):
    url = "https://example.com/org/repo.git"
    with pytest.raises(ValueError) as caught:
        authentication(remote_source(url), profile(url, **auth))
    assert str(caught.value) == "invalid fetch authentication profile or source binding"


def test_ssh_requires_explicit_key_and_host_pin_and_retains_passphrase():
    url = "ssh://git@example.com:2222/org/repo.git";source = remote_source(url)
    with pytest.raises(ValueError):
        authentication(source)
    auth = authentication(source, profile(url, kind="ssh_key", private_key=KEY,
                                          host_keys=[host_key()], passphrase="synthetic-passphrase"))
    assert auth.payload()["known_hosts"] == "[example.com]:2222 " + host_key() + "\n"
    assert auth.payload()["passphrase"] == "synthetic-passphrase"
    assert "synthetic-passphrase" not in repr(auth) and "QUJD" not in repr(auth)


@pytest.mark.parametrize("keys", [[], ["@cert-authority *.example.com ssh-ed25519 AAAA"],
                                  ["ssh-ed25519 AAAA"], [host_key(b"short")], [host_key(), host_key()],
                                  ["ssh-rsa " + host_key().split()[1]]])
def test_unscoped_malformed_or_duplicate_host_keys_rejected(keys):
    url = "git@example.com:org/repo.git"
    with pytest.raises(ValueError):
        authentication(remote_source(url), profile(url, kind="ssh_key", private_key=KEY, host_keys=keys))


@pytest.mark.parametrize("change", [{"schema_version": True}, {"extra": True}, {"authentication": []}])
def test_strict_profile_fields(change):
    url = "https://example.com/org/repo.git";value = profile(url, kind="anonymous");value.update(change)
    with pytest.raises(ValueError):
        authentication(remote_source(url), value)


def test_constructed_source_cannot_bypass_canonical_validation():
    source = remote_source("https://example.com/org/repo.git")
    with pytest.raises(ValueError):
        authentication(replace(source, port=0))


def test_profile_wire_is_bounded_without_real_credentials():
    source = remote_source("https://example.com/org/repo.git")
    value = profile(source.url, kind="bearer", token="x" * 4097)
    with pytest.raises(ValueError):
        authentication(source, value)
    assert len(json.dumps(authentication(source).payload())) < 256


def test_ssh_user_and_path_semantics_are_part_of_profile_binding():
    source = remote_source("git@example.com:org/repo.git")
    for other in ("other@example.com:org/repo.git", "ssh://git@example.com/org/repo.git"):
        with pytest.raises(ValueError):
            authentication(source, profile(other, kind="ssh_key", private_key=KEY, host_keys=[host_key()]))


def test_private_key_bundle_rejected_and_explicit_ca_bundle_scoped():
    url = "git@example.com:org/repo.git"
    with pytest.raises(ValueError):
        authentication(remote_source(url), profile(url, kind="ssh_key", private_key=KEY + KEY, host_keys=[host_key()]))
    ca = "-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n"
    source = remote_source("https://example.com/org/repo.git")
    auth = authentication(source, profile(source.url, kind="anonymous", ca_pem=ca + ca))
    assert auth.payload()["ca_pem"] == ca + ca
    assert "QUJD" not in repr(auth)
