"""Root preparation and recovery enumeration are bounded and caller-free."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_resources as resources, plugin_resource_io as io, plugin_invocation_lock as locks
from test_plugin_resource_journal import Daemon
from worker_test_markers import publish_marker


def blocked_root_lock(connection, *args):
    def root():
        block()
    resources._root = root
    locks._lock_worker(connection, *args)


def block():
    read, write = os.pipe()
    publish_marker(os.environ['OPSKIT_TEST_RESOURCE_ROOT_PID'], str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)


def blocked_inventory(connection, *args):
    stage = os.environ['OPSKIT_TEST_RESOURCE_ROOT_STAGE']
    from network import worker
    original = worker.send
    if stage == 'scan':
        os.scandir = lambda *a, **k: block()
    elif stage == 'root':
        resources._root = block
    else:
        def send(target, value):
            if value.get('ok') is True:
                block()
            original(target, value)
        worker.send = send
    io._inventory_worker(connection, *args)


@pytest.mark.parametrize('stage', ['lease_root', 'root', 'scan', 'response'])
@pytest.mark.parametrize('cancel', [False, True])
def test_root_inventory_timeout_cancel_reaped_without_docker(tmp_path, monkeypatch, expire_worker_after_marker, stage, cancel):
    from network import worker
    marker=tmp_path/'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_ROOT_PID',str(marker))
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_ROOT_STAGE',stage)
    if stage=='lease_root':
        monkeypatch.setattr(locks,'_lock_worker',blocked_root_lock)
    else:
        monkeypatch.setattr(io,'_inventory_worker',blocked_inventory)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker,'time',SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    daemon=Daemon()
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        if stage=='lease_root':
            resources.ResourceLease(['docker'],'container','opskit-'+'a'*32,daemon)
        else:
            resources.reconcile(['docker'],daemon)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert all(call[0]=='info' for call in daemon.calls)
    assert not list(resources._root().glob('*.json'))


def test_caller_does_not_prepare_root_or_enumerate(tmp_path, monkeypatch):
    def reject(*args,**kwargs):
        pytest.fail('caller prepared or enumerated resource root')
    monkeypatch.setattr(resources,'_root',reject)
    daemon=Daemon()
    lease=resources.ResourceLease(['docker'],'container','opskit-'+'a'*32,daemon)
    assert lease.root==tmp_path/'plugin-resources'
    assert resources.reconcile(['docker'],daemon)['active']==1
    lease.close(daemon)
    assert resources.reconcile(['docker'],daemon)=={'recovered':0,'active':0,'pending':0,'invalid':0}


def test_inventory_counts_invalid_names_without_using_them():
    root=resources._root()
    for name in ('unknown.json','z'*32+'.json','a'*32+'.JSON'):
        (root/name).write_bytes(b'keep')
    daemon=Daemon()
    assert resources.reconcile(['docker'],daemon)=={'recovered':0,'active':0,'pending':0,'invalid':3}
    assert daemon.calls==[]
    assert all(path.read_bytes()==b'keep' for path in root.iterdir())


def test_inventory_entry_limit_preserves_evidence():
    root=resources._root()
    for index in range(io.MAX_INVENTORY_ENTRIES+1):
        (root/('retained-'+str(index))).touch()
    with pytest.raises(RuntimeError,match='recovery limit'):
        resources.reconcile(['docker'],Daemon())
    assert len(list(root.iterdir()))==io.MAX_INVENTORY_ENTRIES+1


@pytest.mark.parametrize('field,value',[('names',['../outside.json']),('names',['a'*32+'.json']*2),('invalid',True),('root','relative/plugin-resources'),('request','foreign')])
def test_bad_inventory_frame_refused(monkeypatch,field,value):
    from network import worker
    def run(target,args,**kwargs):
        result={'ok':True,'request':args[0],'root':str(Path(os.environ['OPSKIT_DATA_DIR'])/'plugin-resources'),'names':[],'invalid':0}
        result[field]=value
        return result
    monkeypatch.setattr(worker,'run',run)
    with pytest.raises(RuntimeError):
        io.inventory(timeout=30,limit=256)


@pytest.mark.parametrize('token',['../outside','a'*31,'A'*32,True])
def test_invalid_resource_identity_no_preparation(tmp_path,token):
    with pytest.raises(ValueError):
        with locks.hold(resource=token,timeout=30):
            pytest.fail('invalid token entered business')
    assert not (tmp_path/'plugin-resources').exists()


def bad_lease_path_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(target, result):
        if result.get('ok') is True and 'handle' in result:
            publish_marker(os.environ['OPSKIT_TEST_RESOURCE_ROOT_PID'], str(os.getpid()))
            result['path'] = 'foreign-resource.lock'
        original(target, result)
    worker.send = send
    locks._lock_worker(connection, *args)


def test_bad_lease_path_refused_and_helper_reaped(tmp_path, monkeypatch):
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_ROOT_PID', str(marker))
    monkeypatch.setattr(locks, '_lock_worker', bad_lease_path_worker)
    daemon = Daemon()
    with pytest.raises(RuntimeError, match='path unconfirmed'):
        resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert not list(resources._root().glob('*.json'))


def test_inventory_record_limit_preserves_all_files():
    root = resources._root()
    for index in range(257):
        (root / (format(index, '032x') + '.json')).write_bytes(b'keep')
    daemon = Daemon()
    with pytest.raises(RuntimeError, match='recovery limit'):
        resources.reconcile(['docker'], daemon)
    assert len(list(root.iterdir())) == 257 and daemon.calls == []
