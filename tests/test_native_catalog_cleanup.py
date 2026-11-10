import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from core import native_catalog_cleanup as cleanup
from core.native_catalog_leases import reader
from core.native_runtime_catalog import RuntimeCatalog

_real_cleanup_worker = cleanup._worker


def blocked_cleanup_worker(connection, *arguments):
    from worker_test_markers import publish_marker
    original = cleanup._remove_owned_tree
    def blocked(*args, **kwargs):
        read, write = os.pipe()
        publish_marker(os.environ["OPSKIT_TEST_CATALOG_MARKER"], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
        return original(*args, **kwargs)
    cleanup._remove_owned_tree = blocked
    _real_cleanup_worker(connection, *arguments)


def prepare_catalog(tmp_path, kind):
    from test_native_runtime_prepare import prepare
    source, record = prepare(tmp_path)
    root = cleanup._root(kind)
    catalog = RuntimeCatalog(root)
    old = catalog.publish("demo", source, record, check_identity=lambda *args: True)
    new = catalog.publish("demo", source, record, check_identity=lambda *args: True)
    if kind == "fetch":
        (root / "demo.json").rename(root / "current.json")
    records = cleanup.inspect(kind)
    return root, old, new, next(item for item in records if item["object"] == old)


@pytest.mark.parametrize("kind", ["runtime", "fetch"])
def test_only_unreferenced_recorded_object_can_be_collected(tmp_path, kind):
    root, old, new, item = prepare_catalog(tmp_path, kind)
    current = next(value for value in cleanup.inspect(kind) if value["object"] == new)
    assert current["referenced"] and not item["referenced"]
    with pytest.raises(RuntimeError):
        cleanup.collect(kind, new, current["record_sha256"])
    assert cleanup.collect(kind, old, item["record_sha256"])["removed"]
    assert not (root / "objects" / old).exists()
    assert (root / "objects" / new).is_dir()
    assert not (root / "ownership" / (old + ".json")).exists()


def test_active_reader_prevents_collection(tmp_path):
    root, old, _, item = prepare_catalog(tmp_path, "runtime")
    with reader(root, old):
        with pytest.raises(RuntimeError):
            cleanup.collect("runtime", old, item["record_sha256"])
        assert (root / "objects" / old).exists()
    cleanup.collect("runtime", old, item["record_sha256"])


@pytest.mark.parametrize("change", ["digest", "pointer", "replacement", "hardlink", "record"])
def test_changes_retain_object_and_receipt(tmp_path, change):
    root, old, _, item = prepare_catalog(tmp_path, "runtime")
    target = root / "objects" / old
    digest = item["record_sha256"]
    if change == "digest":
        digest = "0" * 64
    elif change == "pointer":
        (root / "demo.json").write_text("{}")
    elif change == "replacement":
        target.rename(target.with_name("original"))
        target.mkdir()
    elif change == "hardlink":
        os.link(target / "manifest.json", tmp_path / "outside")
    else:
        (root / "ownership" / (old + ".json")).write_text("{}")
    with pytest.raises(RuntimeError):
        cleanup.collect("runtime", old, digest)
    assert target.exists()
    assert (root / "ownership" / (old + ".json")).exists()


def test_unknown_object_is_not_adopted_and_absent_tree_is_repeatable(tmp_path):
    root, old, _, item = prepare_catalog(tmp_path, "runtime")
    unknown = root / "objects" / ("f" * 32)
    unknown.mkdir()
    (unknown / "evidence").write_bytes(b"unknown")
    with pytest.raises(RuntimeError):
        cleanup.collect("runtime", unknown.name, "0" * 64)
    from core.plugin_state import _remove_owned_tree
    from core.native_catalog_ownership import read
    value, _ = read(root, old)
    _remove_owned_tree(root / "objects" / old, tuple(value["identity"]), lambda: None)
    cleanup.collect("runtime", old, item["record_sha256"])
    assert (unknown / "evidence").read_bytes() == b"unknown"


def test_inspection_does_not_initialize_missing_catalog():
    root = cleanup._root("runtime")
    assert cleanup.inspect("runtime") == []
    assert not root.exists()


def _owned_preparation():
    from core import native_catalog_ownership as ownership
    root = cleanup._root('prepare')
    (root / 'objects').mkdir(parents=True)
    token = 'a' * 32
    target = root / 'objects' / token
    target.mkdir()
    ownership.record_creation(root, token)
    _, digest = ownership.validate(root, token)
    return root, token, target, digest


@pytest.mark.skipif(os.name != 'nt', reason='Windows extended path behavior')
def test_collector_handles_long_recorded_tree_and_preserves_unknown(tmp_path):
    from core.native_runtime import absolute_path
    root, token, target, digest = _owned_preparation()
    directory = absolute_path(target / ('d' * 85))
    directory.mkdir()
    item = directory / ('f' * 85)
    item.write_bytes(b'recorded object')
    assert len(str(target / ('d' * 85) / ('f' * 85))) > 260
    unknown = root / 'objects' / ('b' * 32)
    unknown.mkdir()
    (unknown / 'user-data').write_bytes(b'keep')
    assert cleanup.collect('prepare', token, digest)['removed']
    assert not target.exists()
    assert not (root / 'ownership' / (token + '.json')).exists()
    assert (unknown / 'user-data').read_bytes() == b'keep'


def test_preparation_collector_refuses_replaced_object_during_cleanup(tmp_path, monkeypatch):
    root, token, target, digest = _owned_preparation()
    (target / 'owned').write_bytes(b'owned')
    moved = target.with_name('original')
    original = cleanup._remove_owned_tree

    def replace_before_deletion(path, identity, checkpoint, **limits):
        path.rename(moved)
        path.mkdir()
        (path / 'user-data').write_bytes(b'keep')
        return original(path, identity, checkpoint, **limits)

    monkeypatch.setattr(cleanup, '_remove_owned_tree', replace_before_deletion)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        cleanup._collect(root, 'prepare', token, digest)
    assert (target / 'user-data').read_bytes() == b'keep'
    assert (moved / 'owned').read_bytes() == b'owned'
    assert (root / 'ownership' / (token + '.json')).exists()


def test_preparation_collector_refuses_symlink(tmp_path, symlink_capable):
    root, token, target, digest = _owned_preparation()
    outside = tmp_path / 'user-data'
    outside.write_bytes(b'keep')
    (target / 'link').symlink_to(outside)
    with pytest.raises(RuntimeError):
        cleanup.collect('prepare', token, digest)
    assert outside.read_bytes() == b'keep'
    assert (root / 'ownership' / (token + '.json')).exists()


def test_actual_crash_mid_deletion_keeps_receipt_and_allows_retry(tmp_path):
    root, old, new, item = prepare_catalog(tmp_path, "runtime")
    code = (
        "from core import native_catalog_cleanup as c\n"
        "from core.resource_lock import ResourceLock\n"
        "from pathlib import Path\n"
        "import os, sys\n"
        "original = Path.unlink\n"
        "def unlink(path, *args, **kwargs):\n"
        "    result = original(path, *args, **kwargs)\n"
        "    if 'objects' in path.parts: os._exit(77)\n"
        "    return result\n"
        "Path.unlink = unlink\n"
        "root = c._root('runtime')\n"
        "with ResourceLock(lambda: root / '.catalog.lock', label='native catalog').hold():\n"
        "    c._collect(root, 'runtime', sys.argv[1], sys.argv[2])\n"
    )
    result = subprocess.run([sys.executable, "-c", code, old, item["record_sha256"]],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 77, result.stdout + result.stderr
    assert (root / "ownership" / (old + ".json")).exists()
    cleanup.collect("runtime", old, item["record_sha256"])
    assert not (root / "objects" / old).exists()
    assert (root / "objects" / new).is_dir()


def test_collector_holds_catalog_lock_inside_actual_worker(tmp_path, monkeypatch):
    from network.worker import owned_process
    from core.resource_lock import ResourceBusyError
    from core.native_fetch_catalog import FetchCatalog
    import psutil
    root, old, new, item = prepare_catalog(tmp_path, "runtime")
    marker = tmp_path / "collector.pid"
    monkeypatch.setenv("OPSKIT_TEST_CATALOG_MARKER", str(marker))
    with owned_process(blocked_cleanup_worker,
                       ("runtime", "collect", old, item["record_sha256"], 256),
                       label="catalog concurrency probe"):
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        pid = int(marker.read_text())
        assert psutil.pid_exists(pid)
        # The real cleanup worker holds the same OS lock as both catalog APIs.
        with pytest.raises(ResourceBusyError):
            with RuntimeCatalog(root).lock.hold():
                pytest.fail("publication lock acquired during collection")
        with pytest.raises(ResourceBusyError):
            with FetchCatalog(root).lock.hold():
                pytest.fail("fetch lock acquired during collection")
        assert (root / "objects" / old).is_dir()
    assert not psutil.pid_exists(pid)
    cleanup.collect("runtime", old, item["record_sha256"])
    assert (root / "objects" / new).is_dir()


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_collector_is_reaped_and_can_be_retried(tmp_path, monkeypatch,
                                                       expire_worker_after_marker, cancel):
    from network import worker
    import psutil
    root, old, new, item = prepare_catalog(tmp_path, "runtime")
    marker = tmp_path / "blocked-collector.pid"
    monkeypatch.setenv("OPSKIT_TEST_CATALOG_MARKER", str(marker))
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, "_worker", blocked_cleanup_worker)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
        else:
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            cleanup.collect("runtime", old, item["record_sha256"])
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert (root / "objects" / old).is_dir()
    assert (root / "ownership" / (old + ".json")).exists()
    cleanup.collect("runtime", old, item["record_sha256"])
    assert (root / "objects" / new).is_dir()
