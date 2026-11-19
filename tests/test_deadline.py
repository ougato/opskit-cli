"""A shared timeout never grows due to floating-point cancellation."""
from types import SimpleNamespace

import pytest

from core import deadline


def test_remaining_is_bounded_by_original_timeout(monkeypatch):
    tick = [76.923456789]
    monkeypatch.setattr(deadline, "time", SimpleNamespace(monotonic=lambda: tick[0]))
    budget = deadline.Deadline(180, "operation")
    assert 0 < budget.remaining() <= 180
    assert budget.remaining(cap=5) <= 5
    tick[0] += 179
    assert budget.remaining() == pytest.approx(1)
    tick[0] += 2
    with pytest.raises(TimeoutError):
        budget.remaining()
