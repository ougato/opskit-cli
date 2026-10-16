"""Global accounting includes independent providers, callers and old evidence."""
import os

import pytest

from core import plugin_state as state, plugin_state_budget as budget


def write(base, name, size):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.truncate(size)
    return path


def test_providers_callers_backups_unknowns_and_evidence_share_one_budget(tmp_path):
    base = tmp_path.resolve() / "plugin-data"
    for name in ("one/self/data", "two/callers/consumer/data", "two/callers/consumer.previous/data",
                 "two/callers/consumer.repair-unknown.evidence", "unknown/hidden/.cache/data"):
        write(base, name, 10)
    result = budget.reserve_platform_state(base)
    assert result.payload_bytes == result.charged_bytes == 50
    assert result.staging == 0


def test_in_progress_and_unknown_staging_reserve_full_growth(tmp_path):
    base = tmp_path.resolve() / "plugin-data"
    write(base, "one/.state-owned/new/data", 1)
    write(base, "two/callers/.state-unknown/new/data", 2)
    write(base, "one/self/.state-payload/data", 3)
    result = budget.reserve_platform_state(base)
    assert result.payload_bytes == 6
    assert result.staging == 2
    assert result.charged_bytes == 2 * budget.WRAPPER_RESERVATION_BYTES + 3


def test_split_providers_cannot_escape_byte_limit(tmp_path, monkeypatch):
    base = tmp_path.resolve() / "plugin-data"
    write(base, "one/self/data", 60)
    write(base, "two/self/data", 60)
    monkeypatch.setattr(budget, "MAX_PLATFORM_STATE_BYTES", budget.NEW_STATE_RESERVATION_BYTES + 100)
    with pytest.raises(state.StateTransactionError, match="byte quota"):
        budget.reserve_platform_state(base)
    assert (base / "one/self/data").stat().st_size == 60


def test_empty_staging_cannot_escape_slot_limit(tmp_path, monkeypatch):
    base = tmp_path.resolve() / "plugin-data"
    (base / "one/.state-a").mkdir(parents=True)
    (base / "two/.state-b").mkdir(parents=True)
    monkeypatch.setattr(budget, "MAX_PLATFORM_STATE_STAGING", 2)
    with pytest.raises(state.StateTransactionError, match="staging quota"):
        budget.reserve_platform_state(base)


def test_hardlinked_payload_is_refused_not_double_counted(tmp_path):
    base = tmp_path.resolve() / "plugin-data"
    original = write(base, "one/self/data", 1)
    os.link(original, original.with_name("linked"))
    with pytest.raises(state.StateTransactionError, match="linked or special"):
        budget.reserve_platform_state(base)
    assert original.read_bytes() == b"\0"


def test_missing_base_does_not_create_it(tmp_path):
    base = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        budget.reserve_platform_state(base)
    assert not base.exists()


def test_scan_refuses_directory_mutation(tmp_path):
    base = tmp_path.resolve() / "plugin-data"
    write(base, "one/self/data", 1)
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 8:
            write(base, "injected", 1)
    with pytest.raises(state.StateTransactionError, match="directory changed"):
        budget.reserve_platform_state(base, checkpoint=checkpoint)


def test_global_guard_cannot_be_reentered_by_another_allocator():
    from core.resource_lock import ResourceBusyError
    with budget.platform_state_guard():
        with pytest.raises(ResourceBusyError):
            with budget.platform_state_guard():
                pytest.fail("independent allocation bypassed the global OS lock")
    with budget.platform_state_guard():
        pass


def test_entry_headroom_is_reserved_even_for_tiny_payloads(tmp_path, monkeypatch):
    base = tmp_path.resolve() / "plugin-data"
    write(base, "one/self/data", 0)
    monkeypatch.setattr(budget, "MAX_PLATFORM_STATE_ENTRIES", 8193)
    with pytest.raises(state.StateTransactionError, match="entry quota"):
        budget.reserve_platform_state(base)


def limited_preparation_worker(connection, *args):
    from core import plugin_state_prepare
    budget.MAX_PLATFORM_STATE_BYTES = budget.NEW_STATE_RESERVATION_BYTES + 100
    plugin_state_prepare._prepare_worker(connection, *args)


def test_actual_preparation_refuses_other_provider_usage_before_allocation(tmp_path, monkeypatch):
    from core import plugin_sandbox, plugin_state_prepare as preparation
    from state_publication_fixture import state_packet
    base = tmp_path.resolve() / "plugin-data"
    root = base / "one" / "self"
    root.mkdir(parents=True)
    write(base, "two/callers/consumer/data", 101)
    monkeypatch.setattr(preparation, "_prepare_worker", limited_preparation_worker)
    def forbidden(lease):
        pytest.fail("global quota rejection reached allocation callback")
    with pytest.raises(plugin_sandbox.SandboxError, match="quota check failed"):
        preparation.prepare_candidate(state_packet(), root, plugin="one", caller="platform",
                                      timeout=30, on_allocate=forbidden, budget_base=base)
    assert not list(root.parent.glob(".state-*"))
    assert not state.record_path(root).exists()
    assert (base / "two/callers/consumer/data").stat().st_size == 101


def test_global_os_lock_is_held_through_worker_allocation(tmp_path):
    from core import plugin_state_prepare as preparation
    from core.resource_lock import ResourceBusyError
    from state_publication_fixture import state_packet
    base = tmp_path.resolve() / "plugin-data"
    root = base / "one" / "self"
    root.mkdir(parents=True)
    allocated = []
    def observe(lease):
        with pytest.raises(ResourceBusyError):
            with budget.platform_state_guard():
                pytest.fail("allocation released the global reservation lock")
        allocated.append(lease)
    preparation.prepare_candidate(state_packet(), root, plugin="one", caller="platform",
                                  timeout=30, on_allocate=observe, budget_base=base)
    assert len(allocated) == 1
    assert (allocated[0].candidate / "value").read_bytes() == b"new"
    with budget.platform_state_guard(), state.transaction_guard(root):
        assert state.finish_transaction(root, plugin="one", caller="platform") == "aborted"


def overquota_recovery_worker(connection, *args):
    from core import plugin_state_io
    budget.MAX_PLATFORM_STATE_BYTES = 1
    plugin_state_io._finish_worker(connection, *args)


def test_recovery_remains_available_when_global_budget_is_exhausted(tmp_path, monkeypatch):
    from core import plugin_state_io
    base = tmp_path.resolve() / "plugin-data"
    root = base / "one" / "self"
    root.mkdir(parents=True)
    write(base, "one/self/value", 3)
    candidate = base / "one/.state-owned/new"
    candidate.mkdir(parents=True)
    write(candidate, "value", 3)
    state.prepare_record(root, candidate, plugin="one", caller="platform")
    monkeypatch.setattr(plugin_state_io, "_finish_worker", overquota_recovery_worker)
    assert plugin_state_io.finish_supervised(root, plugin="one", caller="platform", timeout=30) == "aborted"
    assert not candidate.exists()
    assert (root / "value").stat().st_size == 3


def test_repair_growth_is_refused_before_evidence_creation_when_overquota(tmp_path, monkeypatch):
    import hashlib
    from core.plugin_state_repair import repair_state_record
    base = tmp_path.resolve() / "plugin-data"
    root = base / "one" / "self"
    root.mkdir(parents=True)
    candidate = base / "one/.state-owned/new"
    candidate.mkdir(parents=True)
    state.prepare_record(root, candidate, plugin="one", caller="platform")
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    monkeypatch.setattr(budget, "MAX_PLATFORM_STATE_BYTES", 1)
    with pytest.raises(state.StateTransactionError, match="byte quota"):
        repair_state_record(root, plugin="one", caller="platform", expected_sha256=hashlib.sha256(raw).hexdigest())
    assert path.read_bytes() == raw
    assert not list(root.parent.glob("self.repair-*"))


def test_new_partition_initialization_cannot_bypass_entry_quota(tmp_path, monkeypatch):
    from core import plugin_state_io
    base = tmp_path.resolve() / "plugin-data"
    write(base, "other/self/value", 1)
    root = base / "one/callers/consumer"
    monkeypatch.setattr(budget, "MAX_PLATFORM_STATE_ENTRIES", 3)
    with budget.platform_state_guard():
        with pytest.raises(state.StateTransactionError, match="entry quota"):
            plugin_state_io._initialize_state_parent(root, "one", "consumer")
    assert not (base / "one").exists()


def test_exact_admission_includes_new_journal_and_wrapper_reservation(tmp_path, monkeypatch):
    from core import plugin_state_prepare as preparation
    from state_publication_fixture import state_packet
    base = tmp_path.resolve() / "plugin-data"
    root = base / "one/self"
    root.mkdir(parents=True)
    write(base, "two/self/value", 100)
    maximum = budget.NEW_STATE_RESERVATION_BYTES + 100
    monkeypatch.setattr(budget, "MAX_PLATFORM_STATE_BYTES", maximum)
    monkeypatch.setattr(preparation, "_prepare_worker", limited_preparation_worker)
    preparation.prepare_candidate(state_packet(), root, plugin="one", caller="platform",
                                  timeout=30, on_allocate=lambda lease: None, budget_base=base)
    with budget.platform_state_guard():
        actual = budget.reserve_platform_state(base, reserve_bytes=0, reserve_entries=0, reserve_staging=0)
    assert actual.charged_bytes <= maximum
