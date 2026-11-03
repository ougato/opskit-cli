"""Preparation ownership survives crashes without adopting unknown trees."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core import native_catalog_cleanup as cleanup
from core.native_preparation import workspace


def test_normal_and_failed_preparations_are_collected():
    with workspace() as target:
        (target / "candidate").mkdir()
        (target / "candidate/input.txt").write_text("synthetic")
        assert cleanup.inspect("prepare")[0]["valid"]
    assert not target.exists()
    assert cleanup.inspect("prepare") == []
    with pytest.raises(ValueError, match="synthetic preparation failure"):
        with workspace() as target:
            (target / "partial.txt").write_text("synthetic")
            raise ValueError("synthetic preparation failure")
    assert not target.exists()
    assert cleanup.inspect("prepare") == []


def test_active_preparation_cannot_be_collected():
    with workspace() as target:
        record, = cleanup.inspect("prepare")
        with pytest.raises(RuntimeError, match="unconfirmed"):
            cleanup.collect("prepare", record["object"], record["record_sha256"])
        assert target.is_dir()


def crashed_preparation():
    result = subprocess.run([sys.executable, "-c",
        "from core.native_preparation import workspace; import os; "
        "scope=workspace(); root=scope.__enter__(); "
        "(root/'partial.txt').write_text('synthetic partial bytes'); os._exit(27)"],
        cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
        capture_output=True, timeout=30)
    assert result.returncode == 27, result.stderr
    record, = cleanup.inspect("prepare")
    return record, cleanup._root("prepare") / "objects" / record["object"]


def test_real_process_crash_can_be_recovered_with_exact_receipt():
    record, target = crashed_preparation()
    with pytest.raises(RuntimeError):
        cleanup.collect("prepare", record["object"], "0" * 64)
    assert target.is_dir()
    result = cleanup.collect("prepare", record["object"], record["record_sha256"])
    assert result == {"catalog": "prepare", "object": record["object"], "removed": True}
    assert not target.exists() and cleanup.inspect("prepare") == []


def test_replaced_directory_and_unknown_objects_are_retained():
    record, target = crashed_preparation()
    target.rename(target.with_name(target.name + "-original"))
    target.mkdir()
    (target / "independent.txt").write_text("keep")
    unknown = target.parent / ("f" * 32)
    unknown.mkdir()
    (unknown / "unknown.txt").write_text("keep")
    with pytest.raises(RuntimeError):
        cleanup.collect("prepare", record["object"], record["record_sha256"])
    assert (target / "independent.txt").read_text() == "keep"
    assert (unknown / "unknown.txt").read_text() == "keep"


def test_retained_unknown_preparations_bound_new_allocations():
    objects = cleanup._root("prepare") / "objects"
    objects.mkdir(parents=True)
    for index in range(8):
        (objects / str(index)).mkdir()
    with pytest.raises(ValueError, match="capacity"):
        with workspace():
            pytest.fail("over-capacity preparation started")


def test_preparation_cleanup_supports_runtime_inventory_above_guest_state_file_limit():
    with workspace() as target:
        sources = target / "ordinary-runtime-files"
        sources.mkdir()
        for index in range(8300):
            (sources / str(index)).write_bytes(b"x")
    assert not target.exists()
    assert cleanup.inspect("prepare") == []
