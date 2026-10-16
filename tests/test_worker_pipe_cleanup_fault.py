"""Fault injection: a pipe close error must not skip other owned resources."""
from types import SimpleNamespace
import pytest
from network import worker


@pytest.mark.parametrize('failed_pipe', range(6))
def test_pipe_close_failure_still_retires_process_and_other_pipes(monkeypatch, failed_pipe):
    cleanup = []
    phase = [False]
    class Connection:
        def __init__(self, index):
            self.index = index
        def close(self):
            if phase[0]:
                cleanup.append(self.index)
                if self.index == failed_pipe:
                    raise OSError('injected close failure')
        def send_bytes(self, value):
            pass
    pipes = [Connection(i) for i in range(6)]
    class Process:
        pid = 123
        sentinel = 456
        alive = True
        closed = False
        def start(self):
            pass
        def is_alive(self):
            return self.alive
        def terminate(self):
            self.alive = False
        kill = terminate
        def join(self, timeout):
            pass
        def close(self):
            self.closed = True
    process = Process()
    pairs = iter((pipes[:2], pipes[2:4], pipes[4:]))
    context = SimpleNamespace(Pipe=lambda **kwargs: next(pairs), Process=lambda **kwargs: process)
    monkeypatch.setattr(worker.multiprocessing, 'get_context', lambda method: context)
    jobs = []
    if worker.os.name == 'nt':
        from core import windows_job
        monkeypatch.setattr(windows_job, 'attach_running', lambda handle: 'owned-job')
        monkeypatch.setattr(windows_job, 'close', jobs.append)
    with pytest.raises(OSError, match='injected close failure'):
        with worker.owned_process(None, (), label='pipe close fault'):
            phase[0] = True
    assert sorted(cleanup) == list(range(6)), 'one pipe failure skipped remaining pipe cleanup'
    assert process.closed and not process.alive, 'pipe failure skipped process retirement'
    if worker.os.name == 'nt':
        assert jobs == ['owned-job']
