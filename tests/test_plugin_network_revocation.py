"""A live proxy cannot retain revoked private grants until its next invocation."""
from contextlib import closing

import psutil
import pytest

from core import config, plugin_inspection as inspection, plugin_sandbox as sandbox
from core import plugin_trust as trust


def save(targets):
    config.save_config({'plugin': {'sandbox': {'timeout': 30, 'private_targets': targets}}})


def test_owned_reader_reloads_private_grants_without_expanding_live_proxy():
    save({'demo': ['peer:443', 'peer:80']})
    with closing(inspection.ContentInspector()) as reader:
        def check():
            return reader.authorize(config.get_config_path(), trust._trust_file(), (), (),
                                    networks=[('demo', ['peer:443', 'peer:80'])], timeout=30)
        assert check() is None
        pid = reader.pid
        save({'demo': ['peer:80', 'peer:443', 'new:80'], 'other': []})
        assert check() is None
        save({'demo': ['peer:80', 'new:80']})
        assert check() == 'network'
        assert reader.pid == pid
    assert not psutil.pid_exists(pid)


def test_outer_network_revocation_invalidates_nested_invocation(monkeypatch):
    save({'outer': ['outer:443'], 'inner': ['inner:80']})
    monkeypatch.setattr(sandbox._invocations, 'network_authorizations',
                        (('outer', ['outer:443']), ('inner', ['inner:80'])), raising=False)
    with sandbox._execution_budget():
        sandbox._check_live_authorizations()
        save({'outer': [], 'inner': ['inner:80']})
        with pytest.raises(sandbox.SandboxError, match='private network authorization revoked'):
            sandbox._check_live_authorizations()


def test_malformed_current_grants_fail_closed_without_disclosure(monkeypatch):
    save({'demo': ['private-secret:443']})
    monkeypatch.setattr(sandbox._invocations, 'network_authorizations',
                        (('demo', ['private-secret:443']),), raising=False)
    with sandbox._execution_budget():
        save({'demo': 'private-secret'})
        with pytest.raises(sandbox.SandboxError) as failure:
            sandbox._check_live_authorizations()
        assert 'private-secret' not in str(failure.value)


@pytest.mark.parametrize('networks', [None, False, {}, [('bad name', ['peer:80'])],
    [('demo', ['peer:80'])] * 257, [('demo', [False])], [('demo', ['x'] * 257)],
    [('demo', ['x' * 513])], [('demo', ['x' * 512] * 33)],
    [('demo', ['x' * 512] * 31)] * 5])
def test_bad_network_snapshot_never_starts_reader(networks, tmp_path):
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(ValueError, match='invalid plugin authorization'):
            reader.authorize(tmp_path, tmp_path, (), (), networks=networks, timeout=30)
        assert reader.pid is None
