"""Fresh narrow settings queries must not block or disclose other grants."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker_test_markers import (marked_process_exists, process_birth, process_identity_exists,
                                 publish_process_marker)

from core import config, plugin_inspection as inspection, plugin_sandbox as sandbox


def _blocked_setting(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast('B')[:length.value]))
    read, write = os.pipe()
    publish_process_marker(Path(request['config']))
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
        identity = {"pid": pid, "birth": process_birth(pid)}
        assert sandbox._read_sandbox_setting('private_targets', 'demo') == ['example.internal:443']
        config.save_config({'plugin': {'sandbox': {'timeout': 20, 'wsl_distribution': 'Ubuntu',
            'private_targets': {'demo': [], 'other': ['private-marker:80']}}}})
        assert sandbox._read_sandbox_setting('private_targets', 'demo') == []
        assert sandbox._invocations.inspector.pid == pid
    assert not process_identity_exists(identity)


def test_standalone_settings_closes_owned_reader(monkeypatch):
    pids = []
    original = inspection.ContentInspector.config_setting
    def observed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        pids.append({"pid": self.pid, "birth": process_birth(self.pid)})
        return result
    monkeypatch.setattr(inspection.ContentInspector, 'config_setting', observed)
    assert sandbox._read_sandbox_setting('private_targets', 'demo') == []
    assert pids and not process_identity_exists(pids[0])
    assert getattr(sandbox._invocations, 'inspector', None) is None


def test_actual_setting_result_cannot_continue_after_parent_deadline(monkeypatch):
    config.save_config({'plugin': {'sandbox': {'timeout': 20}}})
    with sandbox._execution_budget():
        original = inspection.ContentInspector.config_setting
        pid = sandbox._invocations.inspector.pid
        identity = {"pid": pid, "birth": process_birth(pid)}
        def delayed(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            sandbox._invocations.deadline = sandbox.time.monotonic() - 1
            return value
        monkeypatch.setattr(inspection.ContentInspector, 'config_setting', delayed)
        with pytest.raises(sandbox.SandboxError, match='execution deadline'):
            sandbox._read_sandbox_setting('private_targets', 'demo')
    assert not process_identity_exists(identity)


def test_blocked_standalone_setting_reaped(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / 'worker-pid'
    monkeypatch.setattr(config, 'get_config_path', lambda: marker)
    monkeypatch.setattr(inspection, '_inspection_session', _blocked_setting)
    _, observed = expire_worker_after_marker(marker, startup_budget=sandbox.CONFIG_READ_TIMEOUT)
    with pytest.raises(sandbox.SandboxError, match='execution deadline reading sandbox settings'):
        sandbox._read_sandbox_setting('private_targets', 'demo')
    assert observed and not marked_process_exists(marker)


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
        sandbox._read_sandbox_setting('private_targets', 'demo')
    assert not marked_process_exists(marker)


@pytest.mark.parametrize('result', [{}, {'ok': 1, 'value': []},
    {'ok': True, 'value': [], 'private': 'secret'}, {'ok': True, 'value': [False]}])
def test_setting_response_strict_shape_and_types(result):
    with pytest.raises(RuntimeError):
        inspection._setting_value(result, 'private_targets', 'demo')
