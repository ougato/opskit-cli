"""Runtime source scope, source races and real isolated SDK imports."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import zipfile

import pytest

from core import plugin_runtime_inventory as inventory


def copy_sources(tmp_path):
    source = Path(inventory.__file__).parent
    core = tmp_path / "runtime/core"
    for name in inventory.BUNDLED_FILES:
        target = core / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return core


def test_unrelated_code_and_private_resources_never_enter_snapshot(tmp_path):
    core = copy_sources(tmp_path)
    for name in ("updater.py", "plugin_services.py", "privilege.py", "http.py", "scratch.py", "themes/private.yaml"):
        (core / name).write_text("PRIVATE_SENTINEL", encoding="utf-8")
    files = inventory.snapshot(core)
    assert set(files) == {"core/" + name for name in inventory.RUNTIME_FILES}
    assert all(b"PRIVATE_SENTINEL" not in value for value in files.values())
    assert inventory.code_digest(files) == inventory.code_digest(inventory.snapshot(core))


def test_gateway_inventory_and_digest_independent_of_plugin_sdk(tmp_path):
    core = copy_sources(tmp_path)
    sdk_digest = inventory.code_digest(inventory.snapshot(core))
    previous = inventory.gateway_source(core)
    (core / 'plugin_egress.py').write_bytes(previous + b'\n# gateway revision\n')
    assert inventory.gateway_source(core) != previous
    assert inventory.code_digest(inventory.snapshot(core)) == sdk_digest
    assert 'core/plugin_egress.py' not in inventory.snapshot(core)
    assert 'core/plugin_egress.py' in inventory.bundle_snapshot(core)


def test_code_and_resource_changes_invalidate_runtime_digest(tmp_path):
    core = copy_sources(tmp_path)
    previous = inventory.code_digest(inventory.snapshot(core))
    (core / "sdk.py").write_bytes((core / "sdk.py").read_bytes() + b"\n# changed\n")
    code = inventory.code_digest(inventory.snapshot(core))
    assert code != previous
    (core / "locale/en.yaml").write_bytes((core / "locale/en.yaml").read_bytes() + b"\n# changed\n")
    assert inventory.code_digest(inventory.snapshot(core)) != code


@pytest.mark.parametrize("case", ["missing", "hardlink", "oversize", "symlink", "parent_link"])
def test_missing_or_aliased_source_is_not_silently_omitted(tmp_path, monkeypatch, case):
    core = copy_sources(tmp_path)
    source = core / "sdk.py"
    if case == "missing":
        source.unlink()
    elif case == "hardlink":
        os.link(source, tmp_path / "alias.py")
    elif case == "oversize":
        monkeypatch.setattr(inventory, "MAX_SOURCE_BYTES", 10)
    elif case == "symlink":
        target = tmp_path / "sdk-target.py"
        source.rename(target)
        try:
            source.symlink_to(target)
        except OSError:
            pytest.skip("native symlink creation unavailable")
    else:
        target = tmp_path / "core-target"
        core.rename(target)
        try:
            core.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("native directory symlink creation unavailable")
    with pytest.raises((ValueError, FileNotFoundError)):
        inventory.snapshot(core)


def test_total_source_bytes_bounded(tmp_path, monkeypatch):
    core = copy_sources(tmp_path)
    monkeypatch.setattr(inventory, "MAX_CONTEXT_SOURCE_BYTES", 1000)
    with pytest.raises(ValueError, match="inventory exceeds"):
        inventory.snapshot(core)


def test_replaced_file_between_lstat_and_open_is_rejected(tmp_path, monkeypatch):
    core = copy_sources(tmp_path)
    original = inventory.os.open
    source = core / "sdk.py"
    replacement = tmp_path / "replacement.py"
    replacement.write_text("REPLACED", encoding="utf-8")
    def replaced(path, *args, **kwargs):
        if Path(path) == source:
            os.replace(replacement, source)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(inventory.os, "open", replaced)
    with pytest.raises(ValueError, match="changed during snapshot"):
        inventory.snapshot(core)


def test_captured_guest_imports_and_rpc_adapter_without_platform_modules(tmp_path):
    core = copy_sources(tmp_path)
    captured = tmp_path / 'captured'
    for name, content in inventory.snapshot(core).items():
        target = captured / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    code = '''
import importlib.util, json, sys
sys.path.insert(0, sys.argv[1])
sys.path.extend(json.loads(sys.argv[2]))
from core import sdk, plugin_worker
plugin_worker._adapt_sdk('owned')
for name in ('updater', 'privilege', 'http', 'plugin', 'plugin_services', 'plugin_runtime', 'plugin_sandbox', 'plugin_gateway', 'plugin_egress'):
    assert importlib.util.find_spec('core.' + name) is None
assert sdk.ensure_python_package('yaml')
assert sdk.get_service(name='approved').name == 'approved'
calls = []
plugin_worker.broker = lambda *args, **kwargs: calls.append((args, kwargs)) or True
assert sdk.open_service_menu(name='approved', breadcrumb=['one'], context={'test': True})
assert calls[0][0] == ('service.call', 'approved', 'open_menu', [['one'], {'test': True}], {})
assert sdk.current_lang() in ('en', 'zh')
assert sdk.get_color('success')
print(json.dumps({'sdk_exports': sorted(sdk.__all__), 'platform_modules_absent': True}))
'''
    # Match a guest with dependencies but no host editable-install import hook.
    # -I alone still executes site-packages .pth files from the host environment.
    libraries = sorted({sysconfig.get_paths()[key] for key in ("purelib", "platlib")})
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(captured), json.dumps(libraries)],
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    from core.sdk import __all__ as exports
    assert report["sdk_exports"] == sorted(exports)
    assert report["platform_modules_absent"]


@pytest.mark.parametrize("backend", ["pyinstaller", "nuitka"])
@pytest.mark.parametrize("case", ["valid", "extra", "missing", "missing_gateway", "duplicate", "oversize", "badzip"])
def test_frozen_bundle_is_exact_and_matches_source_digest(tmp_path, monkeypatch, backend, case):
    source_core = copy_sources(tmp_path)
    sources = inventory.bundle_snapshot(source_core)
    sdk_sources = inventory.snapshot(source_core)
    gateway = inventory.gateway_source(source_core)
    core = tmp_path / "frozen/core"
    core.mkdir(parents=True)
    bundle = core / inventory.BUNDLE_NAME
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sources.items():
            if case == "missing" and name == "core/sdk.py":
                continue
            if case == "missing_gateway" and name == "core/plugin_egress.py":
                continue
            archive.writestr(name, content)
        if case == "extra":
            archive.writestr("core/updater.py", "DO_NOT_SHIP")
        if case == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("core/sdk.py", "REPLACED")
    if case == "badzip":
        bundle.write_bytes(b"not a zip")
    if case == "oversize":
        monkeypatch.setattr(inventory, "MAX_SOURCE_BYTES", 10)
    if backend == "pyinstaller":
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.setattr(inventory, "__compiled__", True, raising=False)
    if case == "valid":
        assert inventory.snapshot(core) == sdk_sources
        assert inventory.gateway_source(core) == gateway
        assert inventory.code_digest(inventory.snapshot(core)) == inventory.code_digest(sdk_sources)
    else:
        with pytest.raises(ValueError):
            inventory.snapshot(core)
