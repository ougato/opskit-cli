"""A successful exit cannot substitute for a fresh, bounded build output."""
import os
from pathlib import Path
import time

import pytest

from core.plugin_artifacts import _snapshot

def _inspect_output(root, platform, checkpoint):
    return _snapshot(root, checkpoint)


def test_output_counts_nested_files_without_business_rules(tmp_path):
    (tmp_path/"nested").mkdir();(tmp_path/"nested"/"data.bin").write_bytes(b"abc")
    result=_snapshot(tmp_path,lambda:None)
    assert result["files"]==1 and result["bytes"]==3


def test_output_refuses_hardlinks_without_removing_any_files(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"keep")
    os.link(original, tmp_path / "linked")
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "windows", lambda: None)
    assert original.read_bytes() == b"keep"


def test_output_refuses_oversized_logical_file(tmp_path, monkeypatch):
    monkeypatch.setattr("core.plugin_artifacts.MAX_OUTPUT_BYTES", 8)
    (tmp_path / "huge").write_bytes(b"123456789")
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "android", lambda: None)


def test_output_refuses_excess_entries(tmp_path, monkeypatch):
    monkeypatch.setattr("core.plugin_artifacts.MAX_OUTPUT_ENTRIES", 2)
    for name in ("a", "b", "c"):
        (tmp_path / name).write_bytes(b"x")
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "windows", lambda: None)


def test_output_refuses_deep_tree(tmp_path):
    current = tmp_path
    for _ in range(65):
        current = current / "d"
        current.mkdir()
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "windows", lambda: None)


def test_output_inspection_checks_cancellation(tmp_path):
    (tmp_path / "index.html").write_bytes(b"page")
    def cancelled():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _inspect_output(tmp_path, "web-mobile", cancelled)


def test_output_inspection_refuses_replaced_root(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "index.html").write_bytes(b"original")
    replaced = False
    def replace_once():
        nonlocal replaced
        if not replaced:
            replaced = True
            output.rename(tmp_path / "preserved")
            output.mkdir()
            (output / "index.html").write_bytes(b"replacement")
    with pytest.raises(ValueError):
        _inspect_output(output, "web-mobile", replace_once)
    assert (tmp_path / "preserved/index.html").read_bytes() == b"original"


