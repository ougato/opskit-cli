import os
from contextlib import contextmanager

import pytest

from core.native_runtime import inventory, stage, verify


def test_stage_counts_each_copied_file(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a", "b"):
        (source / name).write_text(name)
    destination = tmp_path / "destination"
    updates = []

    @contextmanager
    def progress(total):
        assert total == 2

        def update(count):
            assert len(list(destination.iterdir())) == count
            updates.append(count)

        yield update

    stage(source, destination, inventory(source), copy_progress=progress)
    assert updates == [1, 2]


def test_runtime_snapshot_binds_bytes_and_file_set(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "python.exe").write_bytes(b"known-runtime")
    expected = inventory(root)
    copied = tmp_path / "copy"
    stage(root, copied, expected)
    verify(copied, expected)
    (copied / "extra.pth").write_text("injected startup hook")
    with pytest.raises(ValueError):
        verify(copied, expected)


def test_changed_runtime_not_staged(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    file = root / "python.exe"
    file.write_bytes(b"original")
    expected = inventory(root)
    file.write_bytes(b"modified")
    with pytest.raises(ValueError):
        stage(root, tmp_path / "copy", expected)
    assert not (tmp_path / "copy").exists()


def test_runtime_hardlink_is_rejected(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    original = tmp_path / "outside"
    original.write_bytes(b"outside")
    os.link(original, root / "linked")
    with pytest.raises(ValueError):
        inventory(root)


def test_existing_destination_never_overwritten(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "python.exe").write_bytes(b"original")
    with pytest.raises(ValueError):
        stage(root, root, inventory(root))
    assert (root / "python.exe").read_bytes() == b"original"
def test_empty_state_snapshot_preserves_directories(tmp_path):
    from core.native_runtime import inventory, stage
    source = tmp_path / "state"
    (source / "state" / "empty").mkdir(parents=True)
    assert inventory(source, allow_empty=True) == {}
    destination = tmp_path / "snapshot"
    stage(source, destination, {}, allow_empty=True)
    assert (destination / "state" / "empty").is_dir()
    (source / "unexpected.txt").write_text("new state")
    import pytest
    with pytest.raises(ValueError):
        stage(source, tmp_path / "stale", {}, allow_empty=True)

