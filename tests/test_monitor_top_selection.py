"""Top-N must preserve nullable ordering and inventory order for equal ranks."""
from types import SimpleNamespace

import pytest

from monitor import process_sampling as sampling


@pytest.mark.parametrize("sort_by,field", [("cpu", "cpu_percent"), ("memory", "mem_percent")])
@pytest.mark.parametrize("limit", [1, 20, 32, 33, 257, 10000])
def test_top_keeps_stable_ranks_across_selection_boundary(monkeypatch, sort_by, field, limit):
    values = [None, 0.0, 3.0, 8.0, 3.0, 0.0, None]
    rows = [{"pid": pid, "cpu_percent": values[pid % len(values)],
             "mem_percent": values[-1 - (pid + 2) % len(values)], "rate_status": "available"}
            for pid in range(257)]
    # Explicit policy oracle: known positive ranks, then zero, then unavailable;
    # each rank preserves original inventory order, including the cutoff tie.
    expected = [row for rank in (8.0, 3.0, 0.0, None) for row in rows if row[field] == rank][:limit]
    calls = []
    def inventory():
        calls.append(True)
        return list(rows)
    monkeypatch.setattr(sampling, "ProcessSampler", lambda: SimpleNamespace(inventory=inventory))
    assert sampling.top(limit, sort_by) == expected
    assert calls == [True]


@pytest.mark.parametrize("sort_by", ["cpu", "memory"])
def test_empty_inventory_is_empty_without_sampling_delay(monkeypatch, sort_by):
    monkeypatch.setattr(sampling, "ProcessSampler", lambda: SimpleNamespace(inventory=lambda: []))
    monkeypatch.setattr(sampling.time, "sleep", lambda *args: pytest.fail("empty inventory must not delay"))
    assert sampling.top(20, sort_by) == []


@pytest.mark.parametrize("sort_by", ["cpu", "memory"])
def test_unavailable_inventory_preserves_identity_order(monkeypatch, sort_by):
    rows = [{"pid": pid, "cpu_percent": None, "mem_percent": None, "rate_status": "denied"}
            for pid in (19, 2, 31, 7)]
    monkeypatch.setattr(sampling, "ProcessSampler", lambda: SimpleNamespace(inventory=lambda: list(rows)))
    monkeypatch.setattr(sampling.time, "sleep", lambda *args: pytest.fail("denied inventory must not delay"))
    assert sampling.top(2, sort_by) == rows[:2]
