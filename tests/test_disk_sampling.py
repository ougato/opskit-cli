"""Bounded volume enumeration, response contracts and native worker cleanup."""
import json
import multiprocessing
import os
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from monitor import disk_sampling


ROW = dict(mountpoint="/disk", device="disk0", fstype="test", total=100,
           used=60, free=40, percent=60.0)


def test_collection_preserves_rows_and_skips_inaccessible_volume(monkeypatch):
    monkeypatch.setattr(psutil, "disk_partitions", lambda **kwargs: [
        SimpleNamespace(mountpoint=p, device="disk0", fstype="test")
        for p in ("/denied", "/disk")])
    def usage(path):
        if path == "/denied":
            raise PermissionError()
        return SimpleNamespace(total=100, used=60, free=40, percent=60.0)
    monkeypatch.setattr(psutil, "disk_usage", usage)
    assert disk_sampling._collect() == [ROW]


def test_inventory_quota_checked_before_usage(monkeypatch):
    monkeypatch.setattr(psutil, "disk_partitions", lambda **kwargs: [None] * 257)
    monkeypatch.setattr(psutil, "disk_usage", lambda path: pytest.fail("over quota"))
    with pytest.raises(ValueError, match="quota"):
        disk_sampling._collect()


@pytest.mark.parametrize("change", [
    {"total": -1}, {"used": True}, {"percent": float("nan")},
    {"percent": 101}, {"mountpoint": 7}, {"fstype": "a" * 4097}, {"extra": 1},
])
def test_invalid_result_rejected(monkeypatch, change):
    monkeypatch.setattr("network.worker.run", lambda *a, **k: {"ok": True, "data": [{**ROW, **change}]})
    with pytest.raises(RuntimeError, match="invalid disk"):
        disk_sampling.sample()


@pytest.mark.parametrize("timeout", [0, -1, True, None, float("inf"), float("nan")])
def test_invalid_allowance_does_not_start_worker(monkeypatch, timeout):
    monkeypatch.setattr("network.worker.run", lambda *a, **k: pytest.fail("invalid allowance"))
    with pytest.raises(ValueError):
        disk_sampling.sample(timeout=timeout)


@pytest.mark.parametrize("message", [{"ok": False}, {"ok": True, "data": None},
                                      {"ok": True, "data": [ROW] * 257}])
def test_unavailable_never_looks_like_empty_success(monkeypatch, message):
    monkeypatch.setattr("network.worker.run", lambda *a, **k: message)
    with pytest.raises(RuntimeError, match="unavailable"):
        disk_sampling.sample()


def _blocked_os_worker(connection):
    import time
    marker = Path(os.environ["OPSKIT_TEST_DISK_MARKER"])
    def blocked(*args, **kwargs):
        marker.write_text(json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()}))
        time.sleep(60)
        raise AssertionError("blocked call returned")
    if os.environ["OPSKIT_TEST_DISK_STAGE"] == "enumerate":
        psutil.disk_partitions = blocked
    else:
        psutil.disk_partitions = lambda **kwargs: [SimpleNamespace(mountpoint="/disk", device="disk0", fstype="test")]
        psutil.disk_usage = blocked
    disk_sampling._worker(connection)


@pytest.mark.parametrize("stage", ["enumerate", "usage"])
@pytest.mark.parametrize("reason", ["timeout", "cancel"])
def test_blocked_os_call_reaped(monkeypatch, tmp_path, expire_worker_after_marker, stage, reason):
    marker = tmp_path / "disk-worker.json"
    monkeypatch.setenv("OPSKIT_TEST_DISK_MARKER", str(marker))
    monkeypatch.setenv("OPSKIT_TEST_DISK_STAGE", stage)
    monkeypatch.setattr(disk_sampling, "_worker", _blocked_os_worker)
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    cancel = None
    if reason == "timeout":
        allowance, observed = expire_worker_after_marker(marker)
        error = TimeoutError
    else:
        allowance = 30
        class CancelAfterEntry:
            def is_set(self):
                return marker.exists()
        cancel = CancelAfterEntry()
        error = InterruptedError
    with pytest.raises(error):
        disk_sampling.sample(timeout=allowance, cancel_event=cancel)
    if reason == "timeout":
        assert observed
    identity = json.loads(marker.read_text())
    assert not psutil.pid_exists(identity["pid"]) or psutil.Process(identity["pid"]).create_time() != identity["created"]
    assert {p.pid for p in multiprocessing.active_children()} == before
    assert time.monotonic() - started < 10


def _healthy_worker(connection):
    from network.worker import send
    try:
        send(connection, {"ok": True, "data": [ROW]})
    finally:
        connection.close()


def test_real_worker_transports_valid_sample(monkeypatch):
    monkeypatch.setattr(disk_sampling, "_worker", _healthy_worker)
    assert disk_sampling.sample(timeout=30) == [ROW]


@pytest.mark.parametrize("command", ["disk", "dashboard"])
@pytest.mark.parametrize("error,code", [(TimeoutError("disk sampling deadline exceeded"), 1),
                                      (KeyboardInterrupt(), 130)])
def test_cli_disk_failure_contract(monkeypatch, command, error, code):
    from typer.testing import CliRunner
    from main import app
    def unavailable(**kwargs):
        raise error
    monkeypatch.setattr(disk_sampling, "sample", unavailable)
    result = CliRunner().invoke(app, ["--output", "json", "--non-interactive",
                                      "monitor", command, "--once"])
    assert result.exit_code == code, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert not any(row.get("ok") for row in rows)
    if code == 1:
        assert len(rows) == 1 and rows[0]["error"]["code"] == 1
        assert "disk sampling" in rows[0]["error"]["message"]
