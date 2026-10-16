"""Known lease metadata retirement preserves live and uncertain evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_resource_io as io, plugin_resources as resources, plugin_inspection as inspection
from core.resource_lock import ResourceLock, ResourceBusyError
from test_plugin_resource_journal import Daemon
from worker_test_markers import publish_marker


def lock_file():
    path=resources._root()/('a'*32+'.json')
    path.with_suffix('.lock').touch()
    return path


def retirement_worker(connection,*args):
    from network import worker
    stage=os.environ['OPSKIT_TEST_RESOURCE_RETIRE_STAGE']
    original_unlink,original_exists,original_send=Path.unlink,io._present,worker.send
    def block():
        read,write=os.pipe()
        publish_marker(os.environ['OPSKIT_TEST_RESOURCE_RETIRE_PID'],str(os.getpid()))
        try:
            if stage=='race':
                until=time.monotonic()+20
                while not Path(os.environ['OPSKIT_TEST_RESOURCE_RETIRE_RELEASE']).exists():
                    if time.monotonic()>=until:
                        raise TimeoutError('retirement race release missing')
                    time.sleep(.01)
            else:
                os.read(read,1)
        finally:
            os.close(read); os.close(write)
    def unlink(path,*values,**kwargs):
        if path.name=='a'*32+'.lock' and stage in ('unlink','race'):
            block()
        return original_unlink(path,*values,**kwargs)
    def exists(path):
        if Path(path).name=='a'*32+'.json' and stage=='exists':
            block()
        return original_exists(path)
    def send(target,value):
        if value.get('ok') is True and stage=='response':
            block()
        return original_send(target,value)
    Path.unlink,io._present,worker.send=unlink,exists,send
    io._retire_worker(connection,*args)


@pytest.mark.parametrize('stage',['exists','unlink','response'])
@pytest.mark.parametrize('cancel',[False,True])
@pytest.mark.parametrize('by_token',[False,True])
def test_blocked_retirement_reaped_without_false_confirmation(tmp_path,monkeypatch,expire_worker_after_marker,stage,cancel,by_token):
    from network import worker
    path=lock_file(); marker=tmp_path/'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_RETIRE_PID',str(marker))
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_RETIRE_STAGE',stage)
    monkeypatch.setattr(io,'_retire_worker',retirement_worker)
    if cancel:
        def clock():
            if marker.exists(): raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker,'time',SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        if by_token:
            io.retire_orphan_lock('a'*32,timeout=30)
        else:
            io.retire_lock(path,timeout=30)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert path.with_suffix('.lock').exists()==(stage!='response')
    assert not path.exists()


@pytest.mark.parametrize('kind',['record','token','index'])
def test_live_or_recorded_metadata_is_retained(kind):
    path=lock_file(); lock=path.with_suffix('.lock')
    if kind=='record':
        path.write_bytes(b'unknown evidence')
        assert io.retire_lock(path,timeout=30)=='retained'
        assert path.read_bytes()==b'unknown evidence'
    else:
        held=lock if kind=='token' else path.parent/'.index.lock'
        with ResourceLock(lambda:held,label='legacy holder').hold():
            assert io.retire_lock(path,timeout=30)=='retained'
    assert lock.exists()


def test_retirement_is_idempotent_and_keeps_permanent_index():
    path=lock_file()
    assert io.retire_lock(path,timeout=30)=='retired'
    assert io.retire_lock(path,timeout=30)=='absent'
    assert (path.parent/'.index.lock').exists()


def test_lifecycle_does_not_check_or_unlink_metadata_in_parent(monkeypatch):
    original=io._present
    def exists(path):
        if Path(path).parent.name=='plugin-resources':
            pytest.fail('parent checked resource path')
        return original(path)
    monkeypatch.setattr(io,'_present',exists)
    daemon=Daemon()
    lease=resources.ResourceLease(['docker'],'container','opskit-'+'b'*32,daemon)
    assert resources.reconcile(['docker'],daemon)['active']==1
    lease.close(daemon)
    assert not lease.path.with_suffix('.lock').exists()


@pytest.mark.parametrize('creation',[False,True])
def test_retirement_failure_preserves_primary_error(monkeypatch,creation):
    primary=OSError('owned primary failure')
    def fail(*args,**kwargs): raise primary
    def timeout(*args,**kwargs): raise TimeoutError('metadata timeout')
    monkeypatch.setattr(io,'retire_lock',timeout)
    if creation:
        monkeypatch.setattr(io,'create_record',fail)
        with pytest.raises(OSError) as caught:
            resources.ResourceLease(['docker'],'container','opskit-'+'b'*32,Daemon())
    else:
        lease=resources.ResourceLease(['docker'],'container','opskit-'+'b'*32,Daemon())
        with pytest.raises(OSError) as caught:
            lease.close(fail)
        assert lease.path.exists()
    assert caught.value is primary


def test_missing_record_does_not_poison_reader(tmp_path):
    path=tmp_path/('a'*32+'.json')
    with closing(inspection.ContentInspector()) as reader:
        with pytest.raises(FileNotFoundError): reader.resource_record(path,timeout=30)
        pid=reader.pid
        with pytest.raises(FileNotFoundError): reader.resource_record(path,timeout=30)
        assert reader.pid==pid


def test_missing_after_inventory_is_not_invalid(monkeypatch):
    import json
    from test_plugin_resource_creation import value
    path=lock_file(); path.write_text(json.dumps(value()))
    def missing(*args,**kwargs):
        path.unlink(); raise FileNotFoundError()
    monkeypatch.setattr(resources,'_read_supervised',missing)
    daemon=Daemon()
    assert resources.reconcile(['docker'],daemon)=={'recovered':0,'active':0,'pending':0,'invalid':0}
    assert daemon.calls==[] and not path.with_suffix('.lock').exists()


def test_new_creator_cannot_publish_during_metadata_unlink(tmp_path,monkeypatch):
    path=lock_file(); marker=tmp_path/'worker.pid'; release=tmp_path/'release'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_RETIRE_PID',str(marker))
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_RETIRE_STAGE','race')
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_RETIRE_RELEASE',str(release))
    monkeypatch.setattr(io,'_retire_worker',retirement_worker)
    monkeypatch.setattr(resources,'uuid',SimpleNamespace(uuid4=lambda:SimpleNamespace(hex='a'*32)))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(io.retire_lock,path,timeout=30)
        try:
            until=time.monotonic()+20
            while not marker.exists():
                assert time.monotonic()<until,'retirement never entered unlink'
                time.sleep(.01)
            with pytest.raises(ResourceBusyError):
                resources.ResourceLease(['docker'],'container','opskit-'+'b'*32,Daemon())
            assert not path.exists()
        finally:
            release.write_bytes(b'release')
        assert future.result(timeout=30)=='retired'
    assert not path.with_suffix('.lock').exists()


@pytest.mark.parametrize('timeout',[0,-1,float('nan'),float('inf'),True,None])
def test_invalid_retirement_allowance_has_no_mutation(timeout):
    path=lock_file()
    with pytest.raises(ValueError,match='allowance'):
        io.retire_lock(path,timeout=timeout)
    assert path.with_suffix('.lock').exists()


def test_hardlinked_metadata_preserved(tmp_path):
    path=lock_file(); alias=tmp_path/'alias'
    os.link(path.with_suffix('.lock'),alias)
    with pytest.raises(ValueError,match='changed'):
        io.retire_lock(path,timeout=30)
    assert alias.exists() and path.with_suffix('.lock').exists()


def test_foreign_directory_metadata_preserved(tmp_path):
    path=tmp_path/('a'*32+'.json'); path.with_suffix('.lock').touch()
    with pytest.raises(ValueError,match='changed'):
        io.retire_lock(path,timeout=30)
    assert path.with_suffix('.lock').exists()


def replacement_worker(connection,*args):
    from contextlib import contextmanager
    from core.resource_lock import ResourceLock
    original=ResourceLock.hold
    @contextmanager
    def hold(self):
        with original(self):
            yield
        path=Path(self.path_provider())
        if path.name=='a'*32+'.lock':
            temporary=path.with_suffix('.replacement')
            temporary.write_bytes(b'replacement')
            os.replace(temporary,path)
    ResourceLock.hold=hold
    io._retire_worker(connection,*args)


def test_replaced_metadata_after_unlock_preserved(monkeypatch):
    path=lock_file()
    monkeypatch.setattr(io,'_retire_worker',replacement_worker)
    with pytest.raises(ValueError,match='changed'):
        io.retire_lock(path,timeout=30)
    assert path.with_suffix('.lock').read_bytes()==b'replacement'
