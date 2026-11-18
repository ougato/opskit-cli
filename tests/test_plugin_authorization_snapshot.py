"""A validation reads once; future checks and other threads see revocations."""
import threading

import pytest

from core import plugin_sandbox as sandbox, plugin_trust as trust


def approve():
    trust.grant("caller", "a" * 64, "1")
    trust.grant("provider", "b" * 64, "1")
    trust.grant_service("caller", "a" * 64, "storage", "provider", "b" * 64, 7, ["read"])


def test_reader_predicates_load_store_once(tmp_path, monkeypatch):
    approve()
    calls = []
    original = trust._load
    def counted():
        calls.append(True)
        return original()
    monkeypatch.setattr(trust, "_load", counted)
    identities = (("caller", "a" * 64), ("provider", "b" * 64))
    edges = (("caller", "a" * 64, "storage", "provider", "b" * 64, 7, "read"),)
    assert sandbox._read_live_authorizations(identities, edges) is None
    assert len(calls) == 1
    trust.revoke_service("caller", "storage")
    assert sandbox._read_live_authorizations(identities, edges) == "service"


def test_snapshot_scope_does_not_hide_future_or_cross_thread_revocation(tmp_path):
    approve()
    observed = []
    with trust.authorization_snapshot():
        assert trust.is_trusted("caller", "a" * 64)
        trust.revoke("caller")
        thread = threading.Thread(target=lambda: observed.append(trust.is_trusted("caller", "a" * 64)))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert observed == [False]
    assert not trust.is_trusted("caller", "a" * 64)
    with trust.authorization_snapshot():
        assert not trust.is_trusted("caller", "a" * 64)


def test_exception_cannot_leave_cached_permission(tmp_path):
    approve()
    with pytest.raises(RuntimeError):
        with trust.authorization_snapshot():
            raise RuntimeError("synthetic interruption")
    trust.revoke("caller")
    assert not trust.is_trusted("caller", "a" * 64)


def test_record_returned_to_caller_cannot_change_snapshot_permission(tmp_path):
    approve()
    with trust.authorization_snapshot():
        record = trust.trusted_record("caller")
        record["fingerprint"] = "c" * 64
        record["service_grants"].clear()
        assert trust.is_trusted("caller", "a" * 64)
        assert not trust.is_trusted("caller", "c" * 64)
        assert trust.is_service_authorized("caller", "a" * 64, "storage", "provider", "b" * 64, 7, "read")
