"""Use a real blocked child to prove pipe errors do not skip retirement."""
import os
import time
import multiprocessing
import pytest
from network import worker


def blocked_child(connection):
    worker.send(connection, {'pid': os.getpid()})
    read, write = os.pipe()
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)


def test_real_worker_is_retired_when_receive_close_reports_error(monkeypatch):
    native = multiprocessing.get_context('spawn')
    pairs = [0]
    class Receive:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def close(self):
            self.connection.close()
            raise OSError('injected receive close failure')
    class Context:
        def Process(self, **kwargs):
            return native.Process(**kwargs)
        def Pipe(self, **kwargs):
            receive, transmit = native.Pipe(**kwargs)
            pairs[0] += 1
            return (Receive(receive) if pairs[0] == 1 else receive), transmit
    monkeypatch.setattr(worker.multiprocessing, 'get_context', lambda method: Context())
    child = None
    try:
        with pytest.raises(OSError, match='injected receive close failure'):
            with worker.owned_process(blocked_child, (), label='actual close failure') as (receive, process):
                child = process
                message = worker.wait_result(receive, deadline=time.monotonic() + 30, label='child ready')
                assert message['pid'] == process.pid
        assert child is not None and child._closed, 'real child ownership was not retired'
    finally:
        if child is not None and not child._closed:
            if child.is_alive():
                child.kill()
            child.join(timeout=5)
            child.close()
