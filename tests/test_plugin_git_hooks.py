"""Platform lifecycle Git must not run hooks outside the plugin sandbox."""
import os
import shlex
import subprocess

import pytest

from plugin import commands


@pytest.mark.timeout(300)
@pytest.mark.parametrize('hook_source', ['template', 'repo-default', 'repo-config', 'environment'])
def test_lifecycle_does_not_execute_git_hooks(tmp_path, monkeypatch, prepared_fetch_tools, hook_source):
    operation = 'install' if hook_source == 'template' else 'update'
    origin = tmp_path / 'origin'
    origin.mkdir()
    monkeypatch.setenv('OPSKIT_PLUGINS_DIR', str(tmp_path / 'plugins'))
    # Isolate the test from the developer's actual Git configuration/templates.
    config = tmp_path / 'gitconfig'
    config.write_text('')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    def git(*args):
        return subprocess.run(['git', '-C', str(origin), '-c', 'user.name=test',
                               '-c', 'user.email=test@example.invalid', *args],
                              check=True, capture_output=True, timeout=15)
    git('init', '-q', '-b', 'main')
    (origin / 'plugin.yaml').write_text('name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo\n')
    (origin / 'demo.py').write_text('# initial\n')
    git('add', '.')
    git('commit', '-qm', 'initial')
    marker = tmp_path / 'host-hook-ran'
    template = tmp_path / 'template'
    hooks = template / 'hooks'
    hooks.mkdir(parents=True)
    def hook(path):
        path.write_text('#!/bin/sh\nprintf executed > ' + shlex.quote(marker.as_posix()) + '\n')
        path.chmod(0o755)
    if operation == 'install':
        hook(hooks / 'post-checkout')
        config.write_text('[init]\n\ttemplateDir = "' + template.as_posix() + '"\n')
    else:
        manifest, error = commands.install(str(origin), branch='main', alias='demo')
        assert manifest is not None and not error, error
        commands.grant_trust(manifest)
        if hook_source == 'repo-default':
            hook(origin / '.git' / 'hooks' / 'post-merge')
        else:
            hook(hooks / 'post-merge')
            if hook_source == 'repo-config':
                subprocess.run(['git', '-C', str(origin), 'config',
                                'core.hooksPath', str(hooks)], check=True, capture_output=True, timeout=15)
            else:
                # Replacing an inherited empty value cannot be undone via os.environ on Windows.
                slot = int(os.environ.get('GIT_CONFIG_COUNT') or '0')
                monkeypatch.setenv(f'GIT_CONFIG_KEY_{slot}', 'core.hooksPath')
                monkeypatch.setenv(f'GIT_CONFIG_VALUE_{slot}', str(hooks))
                monkeypatch.setenv('GIT_CONFIG_COUNT', str(slot + 1))
        (origin / 'demo.py').write_text('# updated\n')
        git('commit', '-qam', 'next')
    if operation == 'install':
        manifest, error = commands.install(str(origin), branch='main', alias='demo')
        assert manifest is not None and not error, error
        commands.grant_trust(manifest)
    else:
        ok, error = commands.update(manifest)
        assert ok and error == 'updated'
        assert (manifest.root / 'demo.py').read_text() == '# updated\n'
    assert not (manifest.root / '.git').exists()
    assert not marker.exists(), 'Git hook executed outside sandbox'
