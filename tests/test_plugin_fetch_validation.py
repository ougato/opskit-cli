"""Real supervised reads and bounded paged inventories retain file ownership."""
import json
import os

import pytest

from core import plugin_fetch_candidate as candidate
from core.private_file import temporary_private_file
from tests.test_plugin_fetch_candidate import archive, response
from tests.worker_test_markers import marked_process_exists, publish_process_marker


def test_inventory_larger_than_single_pipe_message_and_response_rewound():
    entries = [(f"src/file-{index:04d}.py", b"raw", {}) for index in range(1000)]
    raw = response(archive(entries), entries=len(entries), size=3000)
    with temporary_private_file() as stream:
        stream.write(raw); stream.flush(); stream.seek(0)
        result = candidate.supervised_inventory(stream, timeout=30)
        assert len(result.files) == 1000 and result.bytes == 3000
        assert result.files[0].path == "src/file-0000.py" and result.files[-1].path == "src/file-0999.py"
        assert not stream.closed and stream.read() == raw


def test_invalid_bytes_rejected_without_disclosure_or_closing_parent():
    with temporary_private_file() as stream:
        stream.write(b"fixture-secret\n"); stream.flush()
        with pytest.raises(ValueError, match="candidate validation rejected") as error:
            candidate.supervised_inventory(stream, timeout=30)
        assert "fixture-secret" not in str(error.value) and not stream.closed


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_budget_does_not_transfer_file(timeout):
    with temporary_private_file() as stream:
        with pytest.raises(ValueError, match="budget"):
            candidate.supervised_inventory(stream, timeout=timeout)


def blocked_worker(connection, *arguments):
    from network import worker
    original_send = worker.send
    original_validate = candidate.validate_output
    def block():
        read, write = os.pipe()
        publish_process_marker(os.environ["OPSKIT_VALIDATION_MARKER"])
        try:
            os.read(read, 1)
        finally:
            os.close(read); os.close(write)
    def validate(source):
        block()
        return original_validate(source)
    def send(connection, value):
        if "page" in value:
            block()
        return original_send(connection, value)
    if os.environ["OPSKIT_VALIDATION_STAGE"] == "read":
        candidate.validate_output = validate
    else:
        worker.send = send
    candidate._validation_worker(connection, *arguments)


@pytest.mark.parametrize("stage", ["read", "page"])
def test_blocked_worker_expires_and_no_partial_inventory_escapes(tmp_path, monkeypatch, expire_worker_after_marker, stage):
    marker = tmp_path / "worker.json"
    monkeypatch.setenv("OPSKIT_VALIDATION_MARKER", str(marker))
    monkeypatch.setenv("OPSKIT_VALIDATION_STAGE", stage)
    monkeypatch.setattr(candidate, "_validation_worker", blocked_worker)
    budget, observed = expire_worker_after_marker(marker)
    with temporary_private_file() as stream:
        stream.write(response(archive([("file", b"raw", {})]))); stream.flush()
        with pytest.raises(TimeoutError):
            candidate.supervised_inventory(stream, timeout=budget)
        assert not stream.closed
    assert observed and not marked_process_exists(marker)


def malformed_worker(connection, *arguments):
    from network import worker
    original = worker.send
    mutation = json.loads(os.environ["OPSKIT_VALIDATION_MUTATION"])
    def send(connection, result):
        if ("page" in result) == mutation["page"]:
            result.update(mutation["value"])
        original(connection, result)
    worker.send = send
    candidate._validation_worker(connection, *arguments)


@pytest.mark.parametrize("mutation", [
    {"page": True, "value": {"request": "wrong"}},
    {"page": True, "value": {"page": True}},
    {"page": True, "value": {"page": 1}},
    {"page": True, "value": {"files": []}},
    {"page": True, "value": {"files": [["file", True, 3, "a" * 64]]}},
    {"page": False, "value": {"count": 2}},
    {"page": False, "value": {"bytes": True}},
    {"page": False, "value": {"commit": "bad"}},
])
def test_invalid_worker_metadata_never_yields_inventory(monkeypatch, mutation):
    monkeypatch.setenv("OPSKIT_VALIDATION_MUTATION", json.dumps(mutation))
    monkeypatch.setattr(candidate, "_validation_worker", malformed_worker)
    with temporary_private_file() as stream:
        stream.write(response(archive([("file", b"raw", {})]))); stream.flush()
        with pytest.raises(RuntimeError, match="invalid candidate validation"):
            candidate.supervised_inventory(stream, timeout=30)
