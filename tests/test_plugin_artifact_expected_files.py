import hashlib

import pytest

from core.plugin_artifacts import _snapshot


def test_snapshot_binds_validated_file_to_receipt(tmp_path):
    path = tmp_path / "environment.json"
    path.write_bytes(b'{"clientVersion":"1.0.1"}')
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    hashes = {}
    snapshot = _snapshot(tmp_path, lambda: None, {"environment.json": expected}, file_hashes=hashes)
    assert snapshot["files"] == 1
    assert hashes == {"environment.json": expected}
    path.write_bytes(b'{"clientVersion":"1.0.2"}')
    with pytest.raises(ValueError, match="hash"):
        _snapshot(tmp_path, lambda: None, {"environment.json": expected})
    with pytest.raises(ValueError, match="missing"):
        _snapshot(tmp_path, lambda: None, {"missing.json": expected})
