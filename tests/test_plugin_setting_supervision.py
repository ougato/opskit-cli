"""Fresh narrow settings queries must not block or disclose other grants."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from core import config, plugin_inspection as inspection, plugin_sandbox as sandbox


def _blocked_setting(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast('B')[:length.value]))
    read, write = os.pipe()
    Path(request['config']).write_text(str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def test_real_settings_fresh_same_reader_and_no_other_plugin_grants():
    config.save_config({'plugin': {'sandbox': {'timeout': 20, 'wsl_distribution': 'Debian',
        'private_targets': {'demo': ['example.internal:443'], 'other': ['private-marker:80']}}}})
    with sandbox._execution_budget():
        pid = sandbox._invocations.inspector.pid
        assert sandbox._read_sandbox_setting('distribution') == 'Debian'
        assert sandbox._read_sandbox_setting('private_targets', 'demo') == ['example.internal:443']
        config.save_config({'plugin': {'sandbox': {'timeout': 20, 'wsl_distribution': 'Ubuntu',
            'private_targets': {'demo': [], 'other': ['private-marker:80']}}}})
        assert sandbox._read_sandbox_setting('distribution') == 'Ubuntu'
        assert sandbox._read_sandbox_setting('private_targets', 'demo') == []
        assert sandbox._invocations.inspector.pid == pid
    assert not psutil.pid_exists(pid)


def test_standalone_settings_closes_owned_reader(monkeypatch):
    pids = []
    original = inspection.ContentInspector.config_setting
    def observed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        pids.append(self.pid)
        return result
    monkeypatch.setattr(inspection.ContentInspector, 'config_setting', observed)
    assert sandbox._read_sandbox_setting('distribution') == 'Debian'
    assert pids and not psutil.pid_exists(pids[0])
    assert getattr(sandbox._invocations, 'inspector', None) is None


def test_native_docker_does_not_query_wsl_settings(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, 'which', lambda value: '/bin/docker')
    monkeypatch.setattr(sandbox, '_read_sandbox_setting', lambda *args: pytest.fail('unneeded query'))
    assert sandbox.docker_command() == ['docker']


def test_wsl_fallback_reads_actual_distribution(monkeypatch):
    config.save_config({'plugin': {'sandbox': {'wsl_distribution': 'Ubuntu-24.04'}}})
    monkeypatch.setattr(sandbox, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(sandbox.shutil, 'which', lambda value: '/bin/wsl' if value == 'wsl' else None)
    assert sandbox.docker_command() == ['wsl', '-d', 'Ubuntu-24.04', '--exec', 'docker']


def test_actual_setting_result_cannot_continue_after_parent_deadline(monkeypatch):
    config.save_config({'plugin': {'sandbox': {'timeout': 20}}})
    with sandbox._execution_budget():
        original = inspection.ContentInspector.config_setting
        pid = sandbox._invocations.inspector.pid
        def delayed(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            sandbox._invocations.deadline = sandbox.time.monotonic() - 1
            return value
        monkeypatch.setattr(inspection.ContentInspector, 'config_setting', delayed)
        with pytest.raises(sandbox.SandboxError, match='execution deadline'):
            sandbox._read_sandbox_setting('private_targets', 'demo')
    assert not psutil.pid_exists(pid)


def test_no_network_does_not_read_private_grants(monkeypatch):
    monkeypatch.setattr(sandbox, '_read_sandbox_setting', lambda *args: pytest.fail('unneeded query'))
    with sandbox._network(SimpleNamespace(permissions=[]), ['docker']) as value:
        assert value == ('none', [])


def test_blocked_standalone_setting_reaped(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / 'worker-pid'
    monkeypatch.setattr(config, 'get_config_path', lambda: marker)
    monkeypatch.setattr(inspection, '_inspection_session', _blocked_setting)
    _, observed = expire_worker_after_marker(marker, startup_budget=sandbox.CONFIG_READ_TIMEOUT)
    with pytest.raises(sandbox.SandboxError, match='execution deadline reading sandbox settings'):
        sandbox._read_sandbox_setting('distribution')
    assert observed and not psutil.pid_exists(int(marker.read_text()))


def test_cancel_setting_reaps_worker(tmp_path, monkeypatch):
    import time
    from network import worker
    marker = tmp_path / 'worker-pid'
    monkeypatch.setattr(config, 'get_config_path', lambda: marker)
    monkeypatch.setattr(inspection, '_inspection_session', _blocked_setting)
    def clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    with pytest.raises(KeyboardInterrupt):
        sandbox._read_sandbox_setting('distribution')
    assert not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize('settings', [
    {'private_targets': []}, {'private_targets': {'demo': 'private-marker'}},
    {'private_targets': {'demo': [True]}}, {'private_targets': {'demo': ['x'] * 257}},
    {'private_targets': {'demo': ['x' * 513]}},
    {'private_targets': {'demo': ['x' * 512] * 33}},
])
def test_bad_private_settings_denied_before_any_network(settings, monkeypatch):
    config.save_config({'plugin': {'sandbox': settings}})
    monkeypatch.setattr(sandbox, '_control', lambda *args: pytest.fail('network created'))
    with pytest.raises(sandbox.SandboxError) as caught:
        with sandbox._network(SimpleNamespace(name='demo', permissions=['network']), ['docker']):
            pytest.fail('bad grants yielded network')
    assert 'private-marker' not in str(caught.value)


@pytest.mark.parametrize('value', [False, [], 'bad name', 'x' * 129])
def test_distribution_validation_uses_real_config(value):
    config.save_config({'plugin': {'sandbox': {'wsl_distribution': value}}})
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_sandbox_setting('distribution')


@pytest.mark.parametrize('result', [{}, {'ok': 1, 'value': []},
    {'ok': True, 'value': [], 'private': 'secret'}, {'ok': True, 'value': [False]}])
def test_setting_response_strict_shape_and_types(result):
    with pytest.raises(RuntimeError):
        inspection._setting_value(result, 'private_targets', 'demo')
