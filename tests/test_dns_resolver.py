"""Real spawned DNS workers must be bounded and reclaimed."""
import multiprocessing
from pathlib import Path
import socket
import time

import pytest

from network import resolver


def slow_worker(connection, host, family, reverse):
    Path(host).write_text("started")
    time.sleep(10)


def test_startup_and_blocking_share_real_deadline(tmp_path, monkeypatch):
    marker = tmp_path / "started"
    before = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(resolver, "_worker", slow_worker)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="DNS deadline"):
        resolver.resolve(str(marker), timeout=2)
    # The production budget includes spawn: expiry before the target starts is
    # also correct. Entry into the blocked stage is proved separately below.
    assert time.monotonic() - started < 4
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_blocked_worker_is_terminated_after_two_real_seconds(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from network import worker
    marker = tmp_path / 'started'
    before = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(resolver, '_worker', slow_worker)
    origin = time.monotonic()
    entered = []

    def stage_clock():
        now = time.monotonic()
        if not entered:
            if marker.exists():
                entered.append(now)
            elif now - origin >= 30:
                pytest.fail('real DNS worker did not enter blocking stage within startup allowance')
        # Only exclude startup in this test. Once the real child writes its
        # marker, the parent's clock advances at the actual wall-clock rate.
        return origin + (now - entered[0] if entered else 0)

    monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=stage_clock))
    with pytest.raises(TimeoutError, match='DNS deadline'):
        resolver.resolve(str(marker), timeout=2)
    assert marker.exists() and entered
    assert 2 <= time.monotonic() - entered[0] < 4
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_cancelled_parent_reclaims_worker(monkeypatch):
    import multiprocessing.connection
    before = {child.pid for child in multiprocessing.active_children()}

    def cancel(self, *args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(multiprocessing.connection._ConnectionBase, "poll", cancel)
    with pytest.raises(KeyboardInterrupt):
        resolver.resolve("localhost", timeout=2)
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_real_system_resolver_preserves_localhost_and_family():
    addresses = resolver.resolve("localhost", family=socket.AF_INET, timeout=5)
    assert "127.0.0.1" in addresses
    assert all(":" not in address for address in addresses)


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf")])
def test_invalid_timeout_does_not_spawn(timeout):
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(ValueError):
        resolver.resolve("localhost", timeout=timeout)
    assert {child.pid for child in multiprocessing.active_children()} == before
