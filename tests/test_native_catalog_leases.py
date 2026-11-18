import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.native_catalog_leases import reader, require_inactive
from core.resource_lock import ResourceBusyError

IDENTITY = "a" * 32


def test_many_readers_pin_same_object_without_serializing_each_other(tmp_path):
    with reader(tmp_path, IDENTITY):
        with reader(tmp_path, IDENTITY):
            assert len(list((tmp_path / "readers" / IDENTITY).iterdir())) == 2
            with pytest.raises(ResourceBusyError):
                require_inactive(tmp_path, IDENTITY)
        with pytest.raises(ResourceBusyError):
            require_inactive(tmp_path, IDENTITY)
    require_inactive(tmp_path, IDENTITY)
    assert list((tmp_path / "readers" / IDENTITY).iterdir()) == []


def test_actual_reader_crash_releases_lease_and_preserves_ordinary_evidence(tmp_path):
    code = (
        "from core.native_catalog_leases import reader\n"
        "from pathlib import Path\n"
        "import os, sys\n"
        "with reader(Path(sys.argv[1]), sys.argv[2]): os._exit(76)\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), IDENTITY],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 76, result.stdout + result.stderr
    root = tmp_path / "readers" / IDENTITY
    assert len(list(root.iterdir())) == 1
    require_inactive(tmp_path, IDENTITY)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("kind", ["unknown", "content", "hardlink"])
def test_unknown_reader_entries_prevent_cleanup(tmp_path, kind):
    root = tmp_path / "readers" / IDENTITY
    root.mkdir(parents=True)
    path = root / (("b" * 32 + ".lock") if kind != "unknown" else "unknown")
    path.write_bytes(b"evidence" if kind == "content" else b"")
    if kind == "hardlink":
        os.link(path, tmp_path / "outside")
    with pytest.raises(ValueError):
        require_inactive(tmp_path, IDENTITY)
    assert path.exists()


def test_catalog_keeps_old_object_pinned_after_pointer_changes(tmp_path):
    from test_native_runtime_prepare import prepare
    from core.native_runtime_catalog import RuntimeCatalog
    runtime, record = prepare(tmp_path)
    catalog = RuntimeCatalog(tmp_path / "catalog")
    old = catalog.publish("demo", runtime, record, check_identity=lambda *args: True)
    with catalog.acquire("demo", fingerprint=record["fingerprint"], sdk_sha256=record["sdk_sha256"]) as (selected, _):
        new = catalog.publish("demo", runtime, record, check_identity=lambda *args: True)
        assert old != new and selected.parent.name == old
        with catalog.lock.hold():
            with pytest.raises(ResourceBusyError):
                require_inactive(catalog.root, old)
        assert selected.is_dir()
    with catalog.lock.hold():
        require_inactive(catalog.root, old)


def test_cross_process_active_reader_refuses_collection_until_exit(tmp_path):
    code = (
        "from core.native_catalog_leases import reader\n"
        "from pathlib import Path\n"
        "import sys\n"
        "with reader(Path(sys.argv[1]), sys.argv[2]):\n"
        "    print('ready', flush=True)\n"
        "    input()\n"
    )
    process = subprocess.Popen([sys.executable, "-u", "-c", code, str(tmp_path), IDENTITY],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with reader(tmp_path, IDENTITY):
            with pytest.raises(ResourceBusyError):
                require_inactive(tmp_path, IDENTITY)
        _, errors = process.communicate("\n", timeout=10)
        assert process.returncode == 0, errors
        require_inactive(tmp_path, IDENTITY)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_abandoned_readers_do_not_permanently_exhaust_capacity(tmp_path, monkeypatch):
    from core import native_catalog_leases as leases
    monkeypatch.setattr(leases, "MAX_READERS", 2)
    root = tmp_path / "readers" / IDENTITY
    root.mkdir(parents=True)
    for token in ("b", "c"):
        (root / (token * 32 + ".lock")).touch()
    with reader(tmp_path, IDENTITY):
        assert len(list(root.iterdir())) == 1
        with reader(tmp_path, IDENTITY):
            with pytest.raises(ValueError, match="capacity"):
                with reader(tmp_path, IDENTITY):
                    pytest.fail("reader capacity exceeded")
    assert list(root.iterdir()) == []
