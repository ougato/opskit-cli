"""Runtime catalogs must remain fresh and reclaimable without forwarding metadata."""
from contextlib import closing
import json
import os
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_inspection as inspection
from core import plugin_sandbox as sandbox
from core import plugin_gateway as gateway
from core.plugin_runtime_inventory import code_digest


def record():
    return {"schema_version": 2, "image": "sha256:" + "a" * 64,
            "fingerprint": "b" * 64, "requirements_sha256": "c" * 64,
            "base_reference": "python@sha256:" + "d" * 64, "code_sha256": code_digest()}


def blocked_catalog(connection, buffer, length, requested):
    from network.worker import send
    while True:
        requested.wait()
        requested.clear()
        request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
        if request["operation"] == "timeout":
            send(connection, {"ok": True, "timeout": 30})
            continue
        read, write = os.pipe()
        Path(request["path"]).write_text(str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
            connection.close()


def test_catalog_is_fresh_same_reader_and_metadata_not_forwarded(tmp_path):
    path = tmp_path / "catalog.json"
    first = record()
    path.write_text(json.dumps({**first, "private_metadata": "must-not-forward"}))
    with closing(inspection.ContentInspector()) as reader:
        assert reader.runtime_catalog(path, timeout=30) == first
        pid = reader.pid
        second = {**first, "fingerprint": "e" * 64}
        path.write_text(json.dumps(second))
        assert reader.runtime_catalog(path, timeout=30) == second
        assert reader.pid == pid
        path.write_text(json.dumps({**second, "code_sha256": "f" * 64}))
        with pytest.raises(RuntimeError, match="catalog inspection failed"):
            reader.runtime_catalog(path, timeout=30)
    assert not psutil.pid_exists(pid)


@pytest.mark.parametrize("case", ["missing", "directory", "hardlink", "oversize", "malformed", "wrong_schema"])
def test_invalid_catalog_never_reaches_docker(tmp_path, monkeypatch, case):
    root = tmp_path / "plugin-runtimes"
    root.mkdir()
    path = root / "demo.json"
    value = record()
    if case == "directory":
        path.mkdir()
    elif case != "missing":
        path.write_text(json.dumps(value))
        if case == "hardlink":
            os.link(path, tmp_path / "alias")
        elif case == "oversize":
            path.write_bytes(b" " * 16385)
        elif case == "malformed":
            path.write_text("{secret-marker")
        elif case == "wrong_schema":
            path.write_text(json.dumps({**value, "schema_version": True}))
    monkeypatch.setattr(sandbox, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox, "_control", lambda *args: pytest.fail("invalid catalog reached Docker"))
    with pytest.raises(sandbox.SandboxError) as caught:
        sandbox._image(SimpleNamespace(name="demo", root=tmp_path), ["docker"])
    assert "secret-marker" not in str(caught.value)


def test_catalog_blocked_read_uses_budget_and_reaps(tmp_path, monkeypatch, expire_worker_after_marker):
    root = tmp_path / "plugin-runtimes"
    root.mkdir()
    marker = root / "demo.json"
    monkeypatch.setattr(sandbox, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(inspection, "_inspection_session", blocked_catalog)
    _, observed = expire_worker_after_marker(marker, startup_budget=30)
    monkeypatch.setattr(sandbox, "_control", lambda *args: pytest.fail("blocked catalog reached Docker"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline reading runtime catalog"):
        sandbox._image(SimpleNamespace(name="demo", root=tmp_path), ["docker"])
    assert observed and not psutil.pid_exists(int(marker.read_text()))
    assert not getattr(sandbox._invocations, "inspection_scope", False)


def test_catalog_cancel_reaps_owned_reader(tmp_path, monkeypatch):
    import time
    from network import worker
    marker = tmp_path / "reader-pid"
    monkeypatch.setattr(inspection, "_inspection_session", blocked_catalog)
    def clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(KeyboardInterrupt):
            reader.runtime_catalog(marker, timeout=30)
    assert not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("change", [{"image": 1}, {"fingerprint": "bad"}, {"base_reference": "latest"}])
def test_response_identity_is_validated_again(change):
    with pytest.raises(ValueError):
        inspection._catalog_value({"ok": True, "record": {**record(), **change}})


def test_catalog_rejects_response_extra_fields():
    with pytest.raises(RuntimeError, match="invalid runtime catalog"):
        inspection._catalog_value({"ok": True, "record": {**record(), "extra": "secret"}})


def test_gateway_catalog_fresh_same_reader_without_host_source_read(tmp_path, monkeypatch):
    import hashlib
    value = {"schema_version": gateway.POLICY, "image": "sha256:" + "a" * 64,
             "base_reference": gateway.BASE_REFERENCE,
             "code_sha256": hashlib.sha256(gateway.gateway_source()).hexdigest()}
    path = tmp_path / ".gateway.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(gateway, "gateway_source", lambda: pytest.fail("host read gateway source"))
    monkeypatch.setattr(gateway, "_catalog", lambda: path)
    monkeypatch.setattr(sandbox, "_control", lambda *args: json.dumps([
        value["image"], str(gateway.POLICY), gateway.BASE_REFERENCE.rsplit("@", 1)[1], value["code_sha256"]]))
    with sandbox._execution_budget():
        pid = sandbox._invocations.inspector.pid
        assert gateway.image(["docker"]) == value["image"]
        value["image"] = "sha256:" + "e" * 64
        path.write_text(json.dumps(value))
        assert gateway.image(["docker"]) == value["image"]
        assert sandbox._invocations.inspector.pid == pid
        path.write_text(json.dumps({**value, "code_sha256": "f" * 64}))
        with pytest.raises(sandbox.SandboxError, match="prepare a platform gateway"):
            gateway.image(["docker"])
    assert not psutil.pid_exists(pid)


def test_gateway_catalog_blocked_read_reaps_before_docker(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / ".gateway.json"
    monkeypatch.setattr(gateway, "_catalog", lambda: marker)
    monkeypatch.setattr(inspection, "_inspection_session", blocked_catalog)
    _, observed = expire_worker_after_marker(marker, startup_budget=30)
    monkeypatch.setattr(sandbox, "_control", lambda *args: pytest.fail("blocked gateway reached Docker"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline reading runtime catalog"):
        gateway.image(["docker"])
    assert observed and not psutil.pid_exists(int(marker.read_text()))
    assert not getattr(sandbox._invocations, "inspection_scope", False)
