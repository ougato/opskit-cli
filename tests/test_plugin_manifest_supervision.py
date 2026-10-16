"""Fresh complete manifests use owned, bounded, cancellable host reads."""
from contextlib import closing, nullcontext
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import publish_marker

from core import plugin, plugin_inspection as inspection, plugin_sandbox as sandbox


BASE = {"name": "demo", "version": "1.0.0", "api_version": 2,
        "kind": "python", "entry": "demo_pkg"}


def write_manifest(root, **changes):
    (root / "plugin.yaml").write_text(json.dumps({**BASE, **changes}, ensure_ascii=False), encoding="utf-8")
    return plugin.load_manifest(root)


def blocked_manifest(connection, buffer, length, requested):
    from network.worker import send
    while True:
        requested.wait()
        requested.clear()
        request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
        if request["operation"] == "timeout":
            send(connection, {"ok": True, "timeout": 30})
            continue
        read, write = os.pipe()
        publish_marker(Path(request["root"]).joinpath("reader-pid"), str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
            connection.close()


def bad_manifest_session(connection, buffer, length, requested):
    from network.worker import send
    requested.wait()
    requested.clear()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    spec = json.loads(Path(request["root"]).joinpath("frames.json").read_text())
    marker = Path(request["root"]) / "reader-pid"
    if not spec.get("block_after_first"):
        publish_marker(marker, str(os.getpid()))
    send(connection, spec["header"])
    if spec.get("block_after_first"):
        view = memoryview(buffer).cast("B")
        view[:] = b"x" * len(view)
        view.release()
        send(connection, {"offset": 0, "size": inspection.REQUEST_LIMIT})
        requested.wait()
        requested.clear()
        publish_marker(marker, str(os.getpid()))
    if "chunk" in spec:
        send(connection, spec["chunk"])
    # Remain live, so a rejection must actually reap the owned process.
    read, write = os.pipe()
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def block_selected_manifest_session(connection, buffer, length, requested):
    original = plugin.load_manifest
    def load(root):
        snapshot = root.parent.name.startswith("opskit-sandbox-")
        if snapshot == (os.environ["OPSKIT_TEST_MANIFEST_BLOCK_STAGE"] == "snapshot"):
            read, write = os.pipe()
            publish_marker(Path(os.environ["OPSKIT_TEST_MANIFEST_BLOCK_MARKER"]), str(os.getpid()))
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
        return original(root)
    plugin.load_manifest = load
    inspection._inspection_session(connection, buffer, length, requested)


def test_complete_large_manifest_and_small_requests_share_fresh_reader(tmp_path, monkeypatch):
    cases = [{}, {"description": {"en": "x" * 100000}}, {"label": {"zh": "中" * 200000}},
             {"kind": "exec", "entry": {"linux": "bin/changed", "windows": "changed.exe"}},
             {"provides": ["storage"], "service_contracts": {"storage": {"api_version": 7, "methods": ["read"]}},
              "uses": [{"service": "other", "provider": "second", "api_version": 2, "methods": ["get"]}]}]
    with closing(inspection.ContentInspector()) as reader:
        pid = None
        for changes in cases * 2:
            expected = write_manifest(tmp_path, **changes)
            # Only the spawned reader may access YAML. Parent reconstruction is pure.
            with monkeypatch.context() as patch:
                patch.setattr(plugin, "load_manifest", lambda *a: pytest.fail("manifest read in host"))
                assert reader.manifest(tmp_path, timeout=30) == expected
            if pid is None:
                pid = reader.pid
            assert reader.pid == pid
            assert reader.inspect(tmp_path, verify_checksums=False, timeout=30)
    assert not psutil.pid_exists(pid)


@pytest.mark.parametrize("invalid", [None, "name: [", json.dumps({**BASE, "name": "platform"}),
                                      json.dumps({**BASE, "description": {"en": "x" * 1100000}})],
                         ids=["missing", "syntax", "reserved-name", "oversize"])
def test_invalid_or_oversize_manifest_closes_reader(tmp_path, invalid):
    if invalid is not None:
        (tmp_path / "plugin.yaml").write_text(invalid)
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(RuntimeError, match="manifest inspection failed"):
            reader.manifest(tmp_path, timeout=30)
        assert reader.pid is None
        with pytest.raises(RuntimeError, match="closed"):
            reader.manifest(tmp_path, timeout=30)


@pytest.mark.parametrize("cancel", [False, True])
def test_actual_blocked_manifest_is_reaped(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    marker = tmp_path / "reader-pid"
    monkeypatch.setattr(inspection, "_inspection_session", blocked_manifest)
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
            reader.manifest(tmp_path, timeout=30)
        assert reader.pid is None
    assert not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("spec", [
    {"header": {"ok": True, "size": True}},
    {"header": {"ok": True, "size": inspection.MANIFEST_RESPONSE_LIMIT + 1}},
    {"header": {"ok": True, "size": 1, "root": "injected"}},
    {"header": {"ok": True, "size": 1}, "chunk": {"offset": 1, "size": 1}},
    {"header": {"ok": True, "size": 1}, "chunk": {"offset": False, "size": 1}},
    {"header": {"ok": True, "size": 1}, "chunk": {"offset": 0, "size": True}},
    {"header": {"ok": True, "size": 1}, "chunk": {"offset": 0, "size": 1, "extra": True}},
])
def test_invalid_transport_rejects_and_reaps(tmp_path, monkeypatch, spec):
    (tmp_path / "frames.json").write_text(json.dumps(spec))
    monkeypatch.setattr(inspection, "_inspection_session", bad_manifest_session)
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(RuntimeError, match="manifest inspection"):
            reader.manifest(tmp_path, timeout=30)
    assert not psutil.pid_exists(int((tmp_path / "reader-pid").read_text()))


@pytest.mark.parametrize("cancel", [False, True])
def test_next_chunk_blocked_uses_same_deadline_and_reaps(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    marker = tmp_path / "reader-pid"
    spec = {"header": {"ok": True, "size": inspection.REQUEST_LIMIT + 1}, "block_after_first": True}
    (tmp_path / "frames.json").write_text(json.dumps(spec))
    monkeypatch.setattr(inspection, "_inspection_session", bad_manifest_session)
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
            reader.manifest(tmp_path, timeout=30)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("stage", ["initial", "snapshot"])
@pytest.mark.parametrize("cancel", [False, True])
def test_invocation_manifest_stages_reap_before_container_start(tmp_path, monkeypatch,
                                                             expire_worker_after_marker, stage, cancel):
    from network import worker
    from core.plugin_trust import compute_fingerprint, grant
    root = tmp_path / "plugin"
    root.mkdir()
    manifest = write_manifest(root)
    grant(manifest.name, compute_fingerprint(root), manifest.version)
    marker = tmp_path / "blocked-reader"
    monkeypatch.setenv("OPSKIT_TEST_MANIFEST_BLOCK_STAGE", stage)
    monkeypatch.setenv("OPSKIT_TEST_MANIFEST_BLOCK_MARKER", str(marker))
    monkeypatch.setattr(inspection, "_inspection_session", block_selected_manifest_session)
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    # This fixture emulates Docker; capability enforcement has dedicated tests.
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda command: None)
    monkeypatch.setattr(sandbox, "_image", lambda *a: "synthetic-image")
    from core.plugin_workspace import allocate
    lease = SimpleNamespace(labels=[], allocate_workspace=allocate)
    monkeypatch.setattr(sandbox, "_resource_scope", lambda *a: nullcontext(lease))
    monkeypatch.setattr(sandbox, "start", lambda *a, **k: pytest.fail("blocked manifest launched container"))
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError,
                       match=None if cancel else "execution deadline reading manifest"):
        sandbox.invoke(manifest, "cli")
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert sandbox._invocations.inspector is None
    assert not getattr(sandbox._invocations, "inspection_scope", False)


@pytest.mark.parametrize("change", [{"root": "/other"}, {"order": True}, {"api_version": True},
                                    {"permissions": ["root"]}, {"name": "platform"}])
def test_manifest_record_cannot_inject_root_or_coerce_identity(tmp_path, change):
    record = asdict(write_manifest(tmp_path))
    record.pop("root")
    with pytest.raises((ValueError, RuntimeError)):
        inspection._manifest_value({**record, **change}, tmp_path)


def test_independent_manifest_read_uses_budget_and_reaps(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "reader-pid"
    monkeypatch.setattr(inspection, "_inspection_session", blocked_manifest)
    _, observed = expire_worker_after_marker(marker)
    with pytest.raises(sandbox.SandboxError, match="execution deadline reading manifest"):
        sandbox._read_manifest(tmp_path)
    assert observed and not psutil.pid_exists(int(marker.read_text()))
    assert not getattr(sandbox._invocations, "inspection_scope", False)


def test_expired_manifest_budget_does_not_start_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox._invocations, "inspection_scope", True, raising=False)
    monkeypatch.setattr(sandbox._invocations, "deadline", time.monotonic() - 1, raising=False)
    monkeypatch.setattr(inspection.ContentInspector, "_start", lambda *a: pytest.fail("started after deadline"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._read_manifest(tmp_path)


def test_invalid_manifest_path_does_not_start_reader():
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(ValueError, match="manifest inspection request"):
            reader.manifest("x" * 65537, timeout=30)
        assert reader.pid is None
