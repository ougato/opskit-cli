"""Native preparation uses isolated interpreter facts and immutable objects."""
import hashlib
from types import SimpleNamespace
import zipfile

import pytest
from packaging.tags import sys_tags
from packaging.markers import default_environment

from core import plugin_runtime as runtime


def inputs(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.mkdir()
    (python / "python.exe").write_bytes(b"not executed on host")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    lock = tmp_path / "lock.txt"
    lines = []
    for name in ("rich", "pyyaml", "typer", "platformdirs", "psutil"):
        wheel = wheels / (name + "-1.0-py3-none-any.whl")
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(name + "-1.0.dist-info/METADATA", "Name: " + name + "\nVersion: 1.0\n")
            archive.writestr(name + "/__init__.py", "raise RuntimeError('never import on host')")
        lines.append(name + "==1.0 --hash=sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest())
    lock.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(runtime, "load_manifest", lambda _: SimpleNamespace(name="demo"))
    monkeypatch.setattr(runtime, "compute_fingerprint", lambda _: "a" * 64)
    monkeypatch.setattr(runtime, "snapshot", lambda: {"core/__init__.py": b""})
    def probe(source, *, staging_parent):
        assert source != python
        assert (source / "python.exe").read_bytes() == b"not executed on host"
        return set(sys_tags()), default_environment()
    monkeypatch.setattr(runtime, "probe", probe)
    return python, wheels, lock


def test_native_prepare_publishes_only_verified_inputs(tmp_path, monkeypatch):
    python, wheels, lock = inputs(tmp_path, monkeypatch)
    identity = runtime.build(tmp_path, [], python, wheels=wheels, lock_file=lock)
    from core.native_runtime_catalog import RuntimeCatalog
    from core.plugin_runtime_inventory import code_digest
    root, record = RuntimeCatalog(tmp_path / "data/native-plugin-runtimes").load(
        "demo", fingerprint="a" * 64, sdk_sha256=code_digest({"core/__init__.py": b""}))
    assert root.parent.name == identity
    assert record["backend"] == "native"
    assert (root / "python/python.exe").read_bytes() == b"not executed on host"


def test_native_prepare_rejects_plugin_change_without_publishing(tmp_path, monkeypatch):
    python, wheels, lock = inputs(tmp_path, monkeypatch)
    identities = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(runtime, "compute_fingerprint", lambda _: next(identities))
    with pytest.raises(PermissionError):
        runtime.build(tmp_path, [], python, wheels=wheels, lock_file=lock)
    assert not (tmp_path / "data/native-plugin-runtimes/demo.json").exists()


def test_native_prepare_requires_native_sdk_dependency(tmp_path, monkeypatch):
    python, wheels, lock = inputs(tmp_path, monkeypatch)
    lock.write_text("\n".join(line for line in lock.read_text().splitlines()
                               if not line.startswith("psutil==")))
    with pytest.raises(ValueError, match="psutil"):
        runtime.build(tmp_path, [], python, wheels=wheels, lock_file=lock)
