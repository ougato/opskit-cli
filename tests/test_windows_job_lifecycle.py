"""Unconfirmed kernel accounting must have a bounded cleanup outcome."""
import ctypes
import os
from types import SimpleNamespace

import pytest

from core import windows_job_lifecycle as lifecycle


@pytest.mark.parametrize('timeout', [True, 0, -1, 31, float('nan'), float('inf'), '5'])
def test_invalid_cleanup_allowance_never_calls_windows(timeout, monkeypatch):
    monkeypatch.setattr(lifecycle.ctypes, 'WinDLL', lambda *a, **k: pytest.fail('invalid budget reached kernel'),
                        raising=False)
    with pytest.raises(ValueError, match='cleanup allowance'):
        lifecycle.terminate_and_wait(1, timeout=timeout)


@pytest.mark.skipif(os.name != 'nt', reason='Windows error conversion required')
@pytest.mark.parametrize('result', ['active', 'query_failed', 'terminate_failed'])
def test_unconfirmed_kernel_tree_cannot_report_cleanup_success(monkeypatch, result):
    calls = []
    def terminate(job, code):
        calls.append(('terminate', job, code))
        return result != 'terminate_failed'
    def query(job, information, output, size, returned):
        calls.append(('query', job, information))
        ctypes.cast(output, ctypes.POINTER(lifecycle.Accounting)).contents.active_processes = 1
        return result != 'query_failed'
    monkeypatch.setattr(lifecycle.ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(
        TerminateJobObject=terminate, QueryInformationJobObject=query))
    ticks = iter([10.0, 10.2])
    monkeypatch.setattr(lifecycle, 'time', SimpleNamespace(monotonic=lambda: next(ticks),
        sleep=lambda seconds: pytest.fail('expired cleanup waited again')))
    with pytest.raises(TimeoutError if result == 'active' else OSError):
        lifecycle.terminate_and_wait(123, timeout=.1)
    assert calls[0] == ('terminate', 123, 1)
    assert len(calls) == (1 if result == 'terminate_failed' else 2)
