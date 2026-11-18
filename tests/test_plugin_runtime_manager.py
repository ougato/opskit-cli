"""Runtime maintenance preserves prepared dependencies and authorization."""
from contextlib import contextmanager
import os
from types import SimpleNamespace

import pytest

from core import native_runtime_sdk_refresh as refresh, plugin_runtime_manager as manager
from core.native_runtime_catalog import RuntimeCatalog, RuntimeSdkOutdated
from core.native_sandbox import NativeSandboxError
from core.plugin_runtime_inventory import code_digest
from tests.test_native_runtime_prepare import prepare


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source, record = prepare(tmp_path)
    root = tmp_path / 'data'
    catalog = RuntimeCatalog(root / 'native-plugin-runtimes')
    old_id = catalog.publish('demo', source, record, check_identity=lambda *args: True)
    sdk = {'core/__init__.py': b'', 'core/plugin_worker.py': b'# new SDK', 'core/new.py': b'new'}
    digest = code_digest(sdk)
    @contextmanager
    def workspace():
        work = tmp_path / 'refresh-work'
        work.mkdir()
        try:
            yield work
        finally:
            import shutil
            shutil.rmtree(work)
    monkeypatch.setattr(refresh, 'workspace', workspace)
    monkeypatch.setattr(refresh, 'snapshot', lambda: sdk)
    monkeypatch.setattr(refresh, 'code_digest', lambda files=None: digest)
    monkeypatch.setattr(manager, 'code_digest', lambda: digest)
    monkeypatch.setattr(manager, 'data_dir', lambda: root)
    return catalog, record, old_id, digest, SimpleNamespace(name='demo')


def test_selected_plugin_refreshes_sdk_without_changing_dependencies(environment):
    catalog, old, old_id, digest, manifest = environment
    with manager.acquire(manifest, old['fingerprint'], checkpoint=lambda: None,
                         authorize=lambda: True) as (runtime, record):
        assert runtime.parent.name != old_id
        assert record['sdk_sha256'] == digest
        assert record['wheels'] == old['wheels'] and record['lock_sha256'] == old['lock_sha256']
        assert {key: value for key, value in record['files'].items() if not key.startswith('site-packages/core/')} == {
            key: value for key, value in old['files'].items() if not key.startswith('site-packages/core/')}
        assert (runtime / 'python/python.exe').read_bytes() == b'operator-provided-interpreter'
        assert (runtime / 'site-packages/example/__init__.py').read_text() == "raise RuntimeError('do not import on host')"
        assert (runtime / 'site-packages/core/new.py').read_bytes() == b'new'
    with manager.acquire(manifest, old['fingerprint'], checkpoint=lambda: None,
                         authorize=lambda: pytest.fail('valid runtime must not refresh')) as (runtime, _):
        assert runtime.parent.name != old_id


def test_speculation_never_updates_runtime(environment):
    catalog, old, old_id, digest, manifest = environment
    with pytest.raises(NativeSandboxError, match='runtime'):
        with manager.acquire(manifest, old['fingerprint'], checkpoint=lambda: None,
                             authorize=lambda: True, speculative=True):
            pytest.fail('stale runtime accepted')
    runtime, _ = catalog.load('demo', fingerprint=old['fingerprint'], sdk_sha256=old['sdk_sha256'])
    assert runtime.parent.name == old_id


@pytest.mark.parametrize('fault', ['damaged', 'plugin_changed', 'authorization_revoked', 'pointer_changed'])
def test_invalid_or_changed_inputs_do_not_publish(environment, monkeypatch, fault):
    catalog, old, old_id, digest, manifest = environment
    before = (catalog.root / 'demo.json').read_bytes()
    if fault == 'damaged':
        (catalog.root / 'objects' / old_id / 'runtime/python/python.exe').write_bytes(b'damaged')
    if fault == 'pointer_changed':
        monkeypatch.setattr(RuntimeCatalog, 'is_current', lambda *args: False)
    fingerprint = 'b' * 64 if fault == 'plugin_changed' else old['fingerprint']
    with pytest.raises((NativeSandboxError, PermissionError)):
        with manager.acquire(manifest, fingerprint, checkpoint=lambda: None,
                             authorize=lambda: fault != 'authorization_revoked'):
            pytest.fail('unsafe runtime accepted')
    assert (catalog.root / 'demo.json').read_bytes() == before


@pytest.mark.parametrize("matches_receipt", [True, False])
def test_selected_installed_update_recovers_verified_old_runtime(environment, monkeypatch, matches_receipt):
    catalog, old, old_id, digest, manifest = environment
    manifest.root = catalog.root.parent / "demo"
    fingerprint = "b" * 64
    trust = {"fingerprint": fingerprint, "source": "reviewed", "installation_receipt": "receipt"}
    monkeypatch.setattr("core.plugin_trust.trusted_record", lambda name: trust)
    monkeypatch.setattr("core.plugin_trust.compute_fingerprint", lambda root: fingerprint)
    monkeypatch.setattr("core.plugin_install_receipt.content_receipt",
                        lambda root: "receipt" if matches_receipt else "modified")
    if matches_receipt:
        with manager.acquire(manifest, fingerprint, checkpoint=lambda: None,
                             authorize=lambda: True) as (runtime, record):
            assert runtime.parent.name != old_id
            assert record["fingerprint"] == fingerprint and record["sdk_sha256"] == digest
            assert record["wheels"] == old["wheels"] and record["lock_sha256"] == old["lock_sha256"]
    else:
        with pytest.raises(NativeSandboxError):
            with manager.acquire(manifest, fingerprint, checkpoint=lambda: None, authorize=lambda: True):
                pytest.fail("modified installation was accepted")
        runtime, _ = catalog.load("demo", fingerprint=old["fingerprint"], sdk_sha256=old["sdk_sha256"])
        assert runtime.parent.name == old_id


def test_only_verified_sdk_mismatch_gets_maintenance_reason(environment):
    catalog, old, old_id, digest, _ = environment
    with pytest.raises(ValueError, match='expected native SDK identity'):
        catalog.load('demo', fingerprint=old['fingerprint'], sdk_sha256=None)
    with pytest.raises(RuntimeSdkOutdated):
        catalog.load('demo', fingerprint=old['fingerprint'], sdk_sha256=digest)
    (catalog.root / 'objects' / old_id / 'runtime/python/python.exe').write_bytes(b'damaged')
    with pytest.raises(ValueError) as error:
        catalog.load('demo', fingerprint=old['fingerprint'], sdk_sha256=digest)
    assert not isinstance(error.value, RuntimeSdkOutdated)


@pytest.mark.skipif(os.name != 'nt', reason='Windows extended path behavior')
def test_sdk_refresh_uses_long_paths_without_changing_original(tmp_path, monkeypatch):
    from core.native_runtime import absolute_path, inventory

    source, record = prepare(tmp_path)
    old_name = 'old_' + 'x' * 120 + '.py'
    new_name = 'new_' + 'y' * 120 + '.py'
    original = absolute_path(source / 'site-packages' / 'core' / old_name)
    original.write_bytes(b'old sdk file')
    record = dict(record, files=inventory(source))
    sdk = {'core/__init__.py': b'', 'core/' + new_name: b'new sdk file'}
    digest = code_digest(sdk)
    work = tmp_path / ('long_workspace_' + 'z' * 70)
    work.mkdir()
    outside = tmp_path / 'user-data'
    outside.write_bytes(b'user data')
    published = []

    @contextmanager
    def workspace():
        yield work

    class Catalog:
        @contextmanager
        def acquire_for_sdk_refresh(self, name, *, fingerprint):
            yield source, record

        def is_current(self, name, runtime, old):
            return runtime == source and old == record

        def publish(self, name, candidate, updated, *, check_identity):
            assert check_identity(name, record['fingerprint']) is True
            assert len(str(work / 'candidate' / 'site-packages' / 'core' / new_name)) > 260
            assert (candidate / 'site-packages' / 'core' / new_name).read_bytes() == b'new sdk file'
            assert not (candidate / 'site-packages' / 'core' / old_name).exists()
            assert inventory(candidate) == updated['files']
            published.append(True)

    monkeypatch.setattr(refresh, 'workspace', workspace)
    monkeypatch.setattr(refresh, 'snapshot', lambda: sdk)
    monkeypatch.setattr(refresh, 'code_digest', lambda files=None: digest)
    refresh.refresh(Catalog(), SimpleNamespace(name='demo'), record['fingerprint'],
                    checkpoint=lambda: None, authorize=lambda: True)
    assert published == [True]
    assert original.read_bytes() == b'old sdk file'
    assert outside.read_bytes() == b'user data'
