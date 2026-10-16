"""The public plugin route cannot restart its deadline after discovery."""
from types import SimpleNamespace

import psutil
import pytest

from core import config, plugin_sandbox as sandbox
from plugin import cli, commands


def test_discovery_cost_cannot_grant_a_fresh_execution_budget(monkeypatch):
    config.save_config({'plugin': {'sandbox': {'timeout': 1}}})
    clock = [100.0]
    monkeypatch.setattr(sandbox, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def discover():
        clock[0] += 2
        return [SimpleNamespace(name='demo')]
    monkeypatch.setattr(cli, 'list_manifests', discover)
    monkeypatch.setattr(cli, 'invoke', lambda *args, **kwargs: pytest.fail('expired discovery reached execution'))
    with pytest.raises(sandbox.SandboxError, match='execution deadline'):
        cli.run_plugin('demo', [])
    assert getattr(sandbox._invocations, 'deadline', None) is None
    assert getattr(sandbox._invocations, 'inspector', None) is None


def test_route_reuses_real_reader_and_does_not_read_config_in_main_process(monkeypatch):
    config.save_config({'plugin': {'sandbox': {'timeout': 20}}})
    observed = []
    def discover():
        observed.append((sandbox._invocations.inspector.pid, sandbox._invocations.deadline))
        return [SimpleNamespace(name='demo')]
    def invoke(manifest, action, *, args):
        with sandbox._execution_budget():
            observed.append((sandbox._invocations.inspector.pid, sandbox._invocations.deadline))
            assert action == 'cli' and args == ['MiXeD', '--help', '--repeat', 'a', '--repeat', 'b']
        return 7
    monkeypatch.setattr(cli, 'list_manifests', discover)
    monkeypatch.setattr(cli, 'invoke', invoke)
    monkeypatch.setattr(commands, 'is_enabled', lambda *args: pytest.fail('unbounded duplicate config read'))
    assert cli.run_plugin('demo', ['MiXeD', '--help', '--repeat', 'a', '--repeat', 'b']) == 7
    assert observed[0][0] == observed[1][0]
    assert observed[1][1] <= observed[0][1]
    assert not psutil.pid_exists(observed[0][0])


def test_disabled_plugin_still_denied_by_execution_authorization(monkeypatch):
    config.save_config({'modules': {'demo': {'enabled': False}}})
    manifest = SimpleNamespace(name='demo')
    monkeypatch.setattr(cli, 'list_manifests', lambda: [manifest])
    monkeypatch.setattr(cli, 'invoke', lambda current, *args, **kwargs: sandbox.check_authorization(current))
    with pytest.raises(sandbox.SandboxError, match='disabled'):
        cli.run_plugin('demo', [])


@pytest.mark.parametrize('names', [[], ['demo', 'demo']])
def test_missing_or_ambiguous_identity_never_reaches_execution(monkeypatch, names):
    monkeypatch.setattr(cli, 'list_manifests', lambda: [SimpleNamespace(name=name) for name in names])
    monkeypatch.setattr(cli, 'invoke', lambda *args, **kwargs: pytest.fail('ambiguous identity executed'))
    with pytest.raises(sandbox.SandboxError, match='identity ambiguous'):
        cli.run_plugin('demo', [])
