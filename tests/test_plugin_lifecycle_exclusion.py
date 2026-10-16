"""Source mutation must respect another CLI's actual invocation OS lock."""
from pathlib import Path
import subprocess
import sys
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
    (root / '.git').mkdir()
    (root / 'plugin.yaml').write_text('name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo\n')
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
        monkeypatch.setattr(commands, 'run', lambda *a, **k: pytest.fail('git ran during active invocation'))
        if operation == 'update':
            ok, error = commands.update(manifest)
            assert not ok and 'busy' in error
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
    assert errors == ['plugin is busy; retry later'] and pauses == [True]


@pytest.mark.parametrize('failure', ['nonzero', 'timeout', 'cancel'])
def test_changed_source_after_failed_git_is_not_authorized(tmp_path, monkeypatch, failure):
    from core import plugin_sandbox as sandbox, plugin_trust as trust
    origin = tmp_path / 'origin'
    origin.mkdir()
    (origin / 'plugin.yaml').write_text('name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo\n')
    (origin / 'demo.py').write_text('# inert version one\n')
    def git(*args):
        subprocess.run(['git','-C',str(origin),'-c','user.name=test','-c','user.email=test@example.invalid',*args],
                       check=True,capture_output=True,timeout=15)
    git('init','-q','-b','main')
    git('add','.')
    git('commit','-qm','initial')
    monkeypatch.setenv('OPSKIT_PLUGINS_DIR',str(tmp_path / 'plugins'))
    monkeypatch.setenv('OPSKIT_DATA_DIR',str(tmp_path / 'state'))
    manifest, error = commands.install(str(origin), branch='main', alias='demo')
    assert manifest is not None and not error
    old_digest = trust.compute_fingerprint(manifest.root)
    trust.grant('demo',old_digest,'1.0.0',str(origin),'main')
    (origin / 'demo.py').write_text('# inert version two\n')
    git('add','.')
    git('commit','-qm','next')
    real_run = commands.run
    def failed_after_actual_pull(command, **kwargs):
        result = real_run(command, **kwargs)
        if command == commands._git_command('pull', '--ff-only', 'origin', 'main'):
            assert result.returncode == 0
            if failure == 'nonzero':
                return subprocess.CompletedProcess(command,1,stdout='',stderr='injected lost success')
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(command,300)
            raise KeyboardInterrupt
        return result
    monkeypatch.setattr(commands,'run',failed_after_actual_pull)
    if failure == 'cancel':
        with pytest.raises(KeyboardInterrupt):
            commands.update(manifest)
    else:
        ok, error = commands.update(manifest)
        assert not ok and error
    new_digest = trust.compute_fingerprint(manifest.root)
    assert new_digest and new_digest != old_digest
    # Current failure paths can leave the old record, but live content checks
    # must still reject execution. This does not prove rollback/recovery.
    assert trust.trusted_record('demo')['fingerprint'] == old_digest
    with pytest.raises(sandbox.SandboxError):
        sandbox.check_authorization(manifest)
