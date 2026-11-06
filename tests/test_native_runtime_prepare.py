import hashlib
import zipfile

import pytest
from packaging.markers import default_environment
from packaging.tags import sys_tags

from core.native_runtime_prepare import assemble, stage_candidate, validate_candidate


def prepare(tmp_path, package="example"):
    source = tmp_path / "python-source"
    source.mkdir()
    (source / "python.exe").write_bytes(b"operator-provided-interpreter")
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n")
        archive.writestr(package + "/__init__.py", "raise RuntimeError('do not import on host')")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    destination = tmp_path / "candidate"
    record = assemble(source, destination, wheels=[wheel],
        lock_text="example==1.0 --hash=sha256:" + digest,
        sdk_files={"core/__init__.py": b"", "core/plugin_worker.py": b"# platform SDK"},
        fingerprint="a" * 64, compatible_tags=set(sys_tags()), marker_environment=default_environment())
    return destination, record


def test_candidate_binds_interpreter_dependencies_sdk_and_plugin(tmp_path):
    root, record = prepare(tmp_path)
    validate_candidate(root, record, fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])
    assert (root / "python/python.exe").read_bytes() == b"operator-provided-interpreter"
    assert "site-packages/core/plugin_worker.py" in record["files"]
    assert record["wheels"]["example"]
    for plugin, sdk in [("b" * 64, record["sdk_sha256"]), ("a" * 64, "0" * 64)]:
        with pytest.raises(ValueError):
            validate_candidate(root, record, fingerprint=plugin, sdk_sha256=sdk)


def test_modified_candidate_cannot_launch(tmp_path):
    root, record = prepare(tmp_path)
    (root / "site-packages/core/plugin_worker.py").write_bytes(b"injected")
    with pytest.raises(ValueError):
        validate_candidate(root, record, fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])


def test_staged_candidate_rejects_stale_identity_and_mutated_source(tmp_path):
    root, record = prepare(tmp_path)
    destination = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="identity"):
        stage_candidate(root, destination, record, fingerprint="b" * 64, sdk_sha256=record["sdk_sha256"])
    assert not destination.exists()
    (root / "site-packages/core/plugin_worker.py").write_bytes(b"injected")
    with pytest.raises(ValueError, match="content"):
        stage_candidate(root, destination, record, fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])
    assert not destination.exists()


def test_staged_candidate_rejects_copy_corruption(tmp_path, monkeypatch):
    import shutil

    root, record = prepare(tmp_path)
    copyfile = shutil.copyfile

    def corrupt_copy(source, destination, **kwargs):
        result = copyfile(source, destination, **kwargs)
        if destination.name == "python.exe":
            destination.write_bytes(b"corrupted after copy")
        return result

    monkeypatch.setattr(shutil, "copyfile", corrupt_copy)
    with pytest.raises(ValueError, match="content"):
        stage_candidate(root, tmp_path / "snapshot", record,
                        fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])


def test_dependency_cannot_replace_sdk(tmp_path):
    with pytest.raises(ValueError, match="conflicts"):
        prepare(tmp_path, package="core")


def test_catalog_publishes_and_rejects_stale_identity(tmp_path):
    from core.native_runtime_catalog import RuntimeCatalog
    root, record = prepare(tmp_path)
    catalog = RuntimeCatalog(tmp_path / "catalog")
    identity = catalog.publish("demo", root, record, check_identity=lambda *args: True)
    loaded, actual = catalog.load("demo", fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])
    assert loaded.parent.name == identity
    assert actual == record
    with pytest.raises(ValueError):
        catalog.load("demo", fingerprint="b" * 64, sdk_sha256=record["sdk_sha256"])


def test_catalog_failed_publication_preserves_old_pointer(tmp_path):
    from core.native_runtime_catalog import RuntimeCatalog
    root, record = prepare(tmp_path)
    catalog = RuntimeCatalog(tmp_path / "catalog")
    catalog.publish("demo", root, record, check_identity=lambda *args: True)
    before = (catalog.root / "demo.json").read_bytes()
    checks = iter([True, False])
    with pytest.raises(PermissionError):
        catalog.publish("demo", root, record, check_identity=lambda *args: next(checks))
    assert (catalog.root / "demo.json").read_bytes() == before
    catalog.load("demo", fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])


def test_catalog_runtime_mutation_rejected(tmp_path):
    from core.native_runtime_catalog import RuntimeCatalog
    root, record = prepare(tmp_path)
    catalog = RuntimeCatalog(tmp_path / "catalog")
    identity = catalog.publish("demo", root, record, check_identity=lambda *args: True)
    (catalog.root / "objects" / identity / "runtime/python/python.exe").write_bytes(b"mutated")
    with pytest.raises(ValueError):
        catalog.load("demo", fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])


@pytest.mark.parametrize("fault", ["traversal", "schema", "manifest_hash", "hardlink", "oversize"])
def test_native_catalog_rejects_invalid_pointer_before_runtime_use(tmp_path, fault):
    import json
    import os
    from core.native_runtime_catalog import RuntimeCatalog
    root, record = prepare(tmp_path)
    catalog = RuntimeCatalog(tmp_path / "catalog")
    catalog.publish("demo", root, record, check_identity=lambda *args: True)
    path = catalog.root / "demo.json"
    pointer = json.loads(path.read_text())
    if fault == "traversal":
        pointer["object"] = "../outside"
    elif fault == "schema":
        pointer["schema_version"] = True
    elif fault == "manifest_hash":
        pointer["manifest_sha256"] = "0" * 64
    elif fault == "hardlink":
        os.link(path, tmp_path / "alias.json")
    if fault == "oversize":
        path.write_bytes(b" " * 4097)
    elif fault != "hardlink":
        path.write_text(json.dumps(pointer))
    with pytest.raises(ValueError):
        catalog.load("demo", fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"])
