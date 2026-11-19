"""Source mutation must respect another CLI's actual invocation OS lock."""
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from core.plugin import load_manifest
from core.process import terminate_tree
from core.resource_lock import ResourceBusyError
from plugin import commands


@pytest.mark.parametrize('operation', ['update', 'remove'])
def test_active_invocation_prevents_source_mutation(tmp_path, monkeypatch, operation):
    plugins = tmp_path / 'plugins'
    root = plugins / 'demo'
    root.mkdir(parents=True)
    (root / 'plugin.yaml').write_text('name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo\n')
    (root / 'demo.py').write_bytes(b'# retained source')
    monkeypatch.setenv('OPSKIT_PLUGINS_DIR', str(plugins))
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(tmp_path / 'state'))
    manifest = load_manifest(root)
    assert manifest is not None
    ready = tmp_path / 'ready'
    program = tmp_path / 'holder.py'
    program.write_text('''import pathlib,sys
sys.path.insert(0, sys.argv[1])
from core.plugin_invocation_lock import hold
def main():
    with hold(plugin='demo', timeout=10):
        pathlib.Path(sys.argv[2]).write_text('locked')
        sys.stdin.readline()
if __name__ == '__main__':
    main()
''')
    process = subprocess.Popen([sys.executable, str(program), str(Path(commands.__file__).parent.parent), str(ready)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            assert process.poll() is None, process.communicate()[1]
            assert time.monotonic() < deadline, 'lock holder did not become ready'
            time.sleep(.02)
        monkeypatch.setattr('core.native_acquisition.populate', lambda *a, **k: pytest.fail('acquisition ran during active invocation'))
        if operation == 'update':
            ok, error = commands.update(manifest)
            assert not ok
            assert error == commands.t('plugin_native.lifecycle.plugin_busy')
        else:
            with pytest.raises(ResourceBusyError):
                commands.remove(manifest)
        assert (root / 'demo.py').read_bytes() == b'# retained source'
        assert ready.read_text() == 'locked' and process.poll() is None
    finally:
        if process.poll() is None:
            try:
                process.communicate(input=b'\n', timeout=10)
            except subprocess.TimeoutExpired:
                terminate_tree(process)
                process.communicate(timeout=5)
        assert process.returncode == 0


def test_remove_menu_reports_busy_without_success(monkeypatch):
    from types import SimpleNamespace
    from plugin import menu
    manifest = SimpleNamespace(name='demo')
    monkeypatch.setattr(menu, '_pick', lambda *args: manifest)
    monkeypatch.setattr(menu, '_display_name', lambda *args: 'demo')
    monkeypatch.setattr(menu, 'confirm', lambda **kwargs: True)
    monkeypatch.setattr(menu, 't', lambda *args, **kwargs: 'remove')
    errors, pauses = [], []
    monkeypatch.setattr(menu, 'print_error', errors.append)
    monkeypatch.setattr(menu, 'print_success', lambda *args: pytest.fail('busy removal reported success'))
    monkeypatch.setattr(menu, 'pause', lambda: pauses.append(True))
    def busy(manifest):
        raise ResourceBusyError('plugin is busy; retry later')
    monkeypatch.setattr(commands, 'remove', busy)
    menu._remove()
    assert errors == ['remove'] and pauses == [True]


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_remove_menu_localizes_real_contention(monkeypatch, language):
    from types import SimpleNamespace
    from core import i18n
    from plugin import menu
    monkeypatch.setattr(i18n, '_current', i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(menu, '_pick', lambda *args: SimpleNamespace(name='demo'))
    monkeypatch.setattr(menu, '_display_name', lambda *args: 'demo')
    monkeypatch.setattr(menu, 'confirm', lambda **kwargs: True)
    errors = []
    monkeypatch.setattr(menu, 'print_error', errors.append)
    monkeypatch.setattr(menu, 'pause', lambda: None)
    def busy(manifest):
        raise ResourceBusyError('plugin is busy; retry later')
    monkeypatch.setattr(commands, 'remove', busy)
    menu._remove()
    assert errors == [i18n.t('plugin_native.lifecycle.plugin_busy')]
    assert ('其他' if language == 'zh' else 'another') in errors[0]


def test_management_waits_for_root_preload_to_release_real_lock(tmp_path, monkeypatch):
    from core import plugin_entry_preload as preload
    from core.plugin_invocation_lock import hold
    from plugin import menu

    root = tmp_path / 'plugins' / 'demo'
    root.mkdir(parents=True)
    (root / 'plugin.yaml').write_text('name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo\n')
    (root / 'demo.py').write_text('# source')
    monkeypatch.setenv('OPSKIT_PLUGINS_DIR', str(root.parent))
    manifest = load_manifest(root)
    acquired, cleanup, allow_cleanup, entered, created = (threading.Event() for _ in range(5))
    session = None

    def target():
        with hold(plugin='demo', timeout=30):
            acquired.set()
            created.wait(30)
            session.cancelled.wait(30)
            cleanup.set()
            allow_cleanup.wait(30)

    session = preload.EntryPreload(manifest, target=target)
    created.set()
    monkeypatch.setattr(preload, '_active', session)
    monkeypatch.setattr(preload, '_discovery', None)
    monkeypatch.setattr(preload, '_discovery_cancel', None)
    monkeypatch.setattr(menu, '_pick', lambda *args: manifest)
    monkeypatch.setattr(menu, '_display_name', lambda *args: 'demo')
    monkeypatch.setattr(menu, 'confirm', lambda **kwargs: True)
    monkeypatch.setattr(menu, 'pause', lambda: None)
    monkeypatch.setattr(menu, 'print_success', lambda *args: None)
    failures = []
    monkeypatch.setattr(menu, 'print_error', failures.append)
    choices = iter(['3', None])
    def select(**kwargs):
        entered.set()
        return next(choices)
    monkeypatch.setattr(menu, 'select', select)
    thread = threading.Thread(target=menu._manage)
    try:
        assert acquired.wait(30)
        thread.start()
        assert cleanup.wait(30)
        assert not entered.wait(.2)
        assert root.exists()
        allow_cleanup.set()
        thread.join(30)
        assert not thread.is_alive()
        assert entered.is_set() and not root.exists() and not failures
    finally:
        allow_cleanup.set()
        session.cancel(wait=True)
        if thread.ident is not None:
            thread.join(30)


@pytest.mark.parametrize('failure', ['nonzero', 'timeout', 'cancel'])
def test_failed_acquisition_preserves_installed_source_and_authorization(tmp_path, monkeypatch, failure):
    from core import plugin_sandbox as sandbox, plugin_trust as trust
    origin = tmp_path / 'origin'
    origin.mkdir()
    (origin / 'plugin.yaml').write_text('name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo\n')
    (origin / 'demo.py').write_text('# inert version one\n')
    def git(*args):
        subprocess.run(['git','-C',str(origin),'-c','user.name=test','-c','user.email=test@example.invalid',*args],
                       check=True,capture_output=True,timeout=15)
    git('init','-q','-b','main')
    git('add','.')
    git('commit','-qm','initial')
    monkeypatch.setenv('OPSKIT_PLUGINS_DIR',str(tmp_path / 'plugins'))
    monkeypatch.setenv('OPSKIT_DATA_DIR',str(tmp_path / 'state'))
    from tests.native_tool_fixtures import populate_synthetic
    monkeypatch.setattr('core.native_acquisition.populate', populate_synthetic)
    manifest, error = commands.install(str(origin), branch='main', alias='demo')
    assert manifest is not None and not error
    old_digest = trust.compute_fingerprint(manifest.root)
    commands.grant_trust(manifest)
    (origin / 'demo.py').write_text('# inert version two\n')
    git('add','.')
    git('commit','-qm','next')
    def failed_after_acquisition(source, destination, **kwargs):
        acquired = populate_synthetic(source, destination, **kwargs)
        assert acquired.fingerprint != old_digest
        if failure == 'nonzero':
            raise RuntimeError('injected lost candidate success')
        if failure == 'timeout':
            raise TimeoutError('injected acquisition timeout')
        raise KeyboardInterrupt
    monkeypatch.setattr('core.native_acquisition.populate', failed_after_acquisition)
    if failure == 'cancel':
        with pytest.raises(KeyboardInterrupt):
            commands.update(manifest)
    else:
        ok, error = commands.update(manifest)
        assert not ok and error
    assert trust.compute_fingerprint(manifest.root) == old_digest
    assert (manifest.root / 'demo.py').read_text() == '# inert version one\n'
    assert trust.trusted_record('demo')['fingerprint'] == old_digest
    sandbox.check_authorization(manifest)
    assert not list(manifest.root.parent.glob('.opskit-update-*'))
