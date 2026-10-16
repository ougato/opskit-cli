"""Complete inventory reads are bounded, fresh and never return partial identities."""
from contextlib import closing
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import marked_process_exists, publish_marker, publish_process_marker

from core import plugin, plugin_inspection as inspection, plugin_sandbox as sandbox
from core import plugin_services as services


def make_plugin(root, directory="demo", name="demo", **changes):
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    record = {"name": name, "version": "1.0.0", "api_version": 2, "kind": "python", "entry": "demo_pkg", **changes}
    (target / "plugin.yaml").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    (target / "demo_pkg").mkdir(exist_ok=True)
    (target / "demo_pkg" / "__init__.py").write_text("raise RuntimeError('inventory must never import plugin code')")
    return plugin.load_manifest(target)


def blocked_scan_session(connection, buffer, length, requested):
    def blocked(path):
        read, write = os.pipe()
        publish_process_marker(Path(path, "reader-pid"))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    plugin.os.scandir = blocked
    inspection._inspection_session(connection, buffer, length, requested)


def fake_inventory_session(connection, buffer, length, requested):
    from network.worker import send
    requested.wait()
    requested.clear()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    root = Path(request["root"])
    spec = json.loads((root / "frames.json").read_text())
    if not spec.get("block_after_first"):
        publish_marker((root / "reader-pid"), str(os.getpid()))
    send(connection, spec["header"])
    view = memoryview(buffer).cast("B")
    for index, record in enumerate(spec.get("records", [])):
        inspection._send_record(connection, view, requested, record)
        if index + 1 < spec["header"]["count"]:
            requested.wait()
            requested.clear()
    if spec.get("block_after_first"):
        publish_marker((root / "reader-pid"), str(os.getpid()))
    read, write = os.pipe()
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        view.release()
        connection.close()


def test_full_inventory_streams_large_manifests_and_preserves_order(tmp_path, monkeypatch):
    make_plugin(tmp_path, "z", "last", label={"zh": "中" * 200000})
    make_plugin(tmp_path, "a", "first", description={"en": "x" * 100000})
    make_plugin(tmp_path, "m", "middle")
    expected = plugin._list_manifests(tmp_path)
    with closing(inspection.ContentInspector()) as reader:
        for _ in range(2):
            with monkeypatch.context() as patch:
                patch.setattr(plugin, "load_manifest", lambda *a: pytest.fail("host read manifest"))
                patch.setattr(plugin, "_list_manifests", lambda *a, **k: pytest.fail("host scanned inventory"))
                assert reader.inventory(tmp_path, timeout=30) == expected
            pid = reader.pid
            assert reader.manifest(tmp_path / "m", timeout=30) == expected[1]
            assert reader.pid == pid
    assert not psutil.pid_exists(pid)


def test_missing_inventory_then_added_and_changed_same_reader(tmp_path):
    root = tmp_path / "plugins"
    with closing(inspection.ContentInspector()) as reader:
        assert reader.inventory(root, timeout=30) == []
        pid = reader.pid
        expected = make_plugin(root, provides=["storage"])
        assert reader.inventory(root, select="service", value="storage", timeout=30) == [expected]
        changed = make_plugin(root, provides=["other"])
        assert reader.inventory(root, select="service", value="storage", timeout=30) == []
        assert reader.inventory(root, select="name", value="demo", timeout=30) == [changed]
        assert reader.pid == pid
    assert not psutil.pid_exists(pid)


def test_inventory_total_is_not_truncated_to_single_manifest_transport_limit(tmp_path):
    label = "中" * 200000
    assert len(label.encode("utf-8")) * 15 > inspection.MANIFEST_RESPONSE_LIMIT
    for index in range(15):
        make_plugin(tmp_path, f"item_{index:02d}", f"item_{index:02d}", label={"zh": label})
    with closing(inspection.ContentInspector()) as reader:
        manifests = reader.inventory(tmp_path, timeout=30)
        pid = reader.pid
    assert [m.name for m in manifests] == [f"item_{index:02d}" for index in range(15)]
    assert all(m.label == {"zh": label} for m in manifests)
    assert not psutil.pid_exists(pid)


def test_service_selection_transports_only_matches_but_keeps_duplicate_identities(tmp_path, monkeypatch):
    one = make_plugin(tmp_path, "one", "provider", provides=["storage"])
    two = make_plugin(tmp_path, "two", "provider", provides=["storage"])
    make_plugin(tmp_path, "unrelated", "other", label={"en": "x" * 100000})
    decoded = []
    original = inspection._manifest_value
    def decode(record, root):
        decoded.append(record["name"])
        return original(record, root)
    monkeypatch.setattr(inspection, "_manifest_value", decode)
    with closing(inspection.ContentInspector()) as reader:
        assert reader.inventory(tmp_path, select="service", value="storage", timeout=30) == [one, two]
    assert decoded == ["provider", "provider"]
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    assert services._find_provider("storage") is None


def test_duplicate_caller_is_denied_before_authorization_or_provider_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    provider = make_plugin(tmp_path, "p", "provider", provides=["storage"],
                           service_contracts={"storage": {"api_version": 7, "methods": ["read"]}})
    requirement = [{"service": "storage", "provider": "provider", "api_version": 7, "methods": ["read"]}]
    make_plugin(tmp_path, "caller_one", "caller", uses=requirement)
    make_plugin(tmp_path, "caller_two", "caller", uses=requirement)
    monkeypatch.setattr(sandbox, "check_authorization", lambda *a: pytest.fail("ambiguous caller authorized"))
    with pytest.raises(sandbox.SandboxError, match="caller identity is ambiguous"):
        services.resolve_service_access(provider, "storage", "read", "caller")


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("entry", ["inventory", "service"])
def test_actual_blocked_scan_reaps_and_cannot_return_empty_inventory(tmp_path, monkeypatch,
                                                                   expire_worker_after_marker, cancel, entry):
    from network import worker
    marker = tmp_path / "reader-pid"
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setattr(inspection, "_inspection_session", blocked_scan_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    run = plugin.list_manifests if entry == "inventory" else lambda: services.call_service("storage", "read")
    monkeypatch.setattr(sandbox, "invoke", lambda *a, **k: pytest.fail("blocked discovery invoked provider"))
    with pytest.raises(KeyboardInterrupt if cancel else plugin.PluginInventoryError,
                       match=None if cancel else "inventory inspection deadline"):
        run()
    assert marker.exists()
    assert not marked_process_exists(marker), marker.read_text()
    assert sandbox._invocations.inspector is None


@pytest.mark.parametrize("header", [{"ok": True, "count": True}, {"ok": True, "count": -1},
                                     {"ok": True, "count": 257}, {"ok": True, "count": 0, "extra": True},
                                     {"ok": False, "reason": "synthetic-sensitive-marker"}])
def test_invalid_inventory_header_rejects_and_reaps(tmp_path, monkeypatch, header):
    (tmp_path / "frames.json").write_text(json.dumps({"header": header}))
    monkeypatch.setattr(inspection, "_inspection_session", fake_inventory_session)
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(RuntimeError, match="invalid plugin inventory inspection result"):
            reader.inventory(tmp_path, timeout=30)
    assert not psutil.pid_exists(int((tmp_path / "reader-pid").read_text()))


@pytest.mark.parametrize("directory", ["../escape", "/absolute", "nested/child", "C:escape", "_hidden", "."])
def test_response_cannot_inject_inventory_directory(tmp_path, monkeypatch, directory):
    record = asdict(make_plugin(tmp_path))
    record.pop("root")
    spec = {"header": {"ok": True, "count": 1}, "records": [{"directory": directory, "manifest": record}]}
    (tmp_path / "frames.json").write_text(json.dumps(spec))
    monkeypatch.setattr(inspection, "_inspection_session", fake_inventory_session)
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(RuntimeError, match="directory identity"):
            reader.inventory(tmp_path, timeout=30)
    assert not psutil.pid_exists(int((tmp_path / "reader-pid").read_text()))


@pytest.mark.parametrize("cancel", [False, True])
def test_incomplete_inventory_cannot_return_first_identity(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    marker = tmp_path / "reader-pid"
    record = asdict(make_plugin(tmp_path))
    record.pop("root")
    spec = {"header": {"ok": True, "count": 2}, "records": [{"directory": "demo", "manifest": record}],
            "block_after_first": True}
    (tmp_path / "frames.json").write_text(json.dumps(spec))
    monkeypatch.setattr(inspection, "_inspection_session", fake_inventory_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            reader.inventory(tmp_path, timeout=30)
    assert not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("select,value", [("all", "unexpected"), ("name", "../escape"), ("service", ""), ([], "")])
def test_invalid_selector_does_not_start_reader(tmp_path, select, value):
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(ValueError, match="inventory inspection request"):
            reader.inventory(tmp_path, select=select, value=value, timeout=30)
        assert reader.pid is None
