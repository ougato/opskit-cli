import io
import pytest

from core.native_fetch import _worker_failure
from core.native_sandbox import NativeSandboxError


def test_fixed_git_failure_reports_operation_and_code_only(monkeypatch):
    from core import i18n
    monkeypatch.setattr(i18n, '_current', i18n._flatten(i18n._load_lang('zh')))
    with pytest.raises(NativeSandboxError) as caught:
        _worker_failure(io.BytesIO(b'OPSKIT_FETCH_ERROR:{"operation":"rev-parse","code":3221225781}\n'), 1)
    assert caught.value.code == 'fetch_git_command_failed'
    assert '3221225781' in str(caught.value)


@pytest.mark.parametrize('body', [b'secret stderr', b'OPSKIT_FETCH_ERROR:{"operation":"private-path","code":1}',
                                  b'OPSKIT_FETCH_ERROR:{"operation":"rev-parse","code":"password"}',
                                  b'OPSKIT_FETCH_ERROR:[]', b'OPSKIT_FETCH_ERROR:' + b'x' * 1000])
def test_untrusted_worker_diagnostics_are_not_displayed(body):
    _worker_failure(io.BytesIO(body), 1)


def test_git_nonzero_exit_keeps_fixed_step_and_numeric_code(tmp_path, monkeypatch):
    import time
    from core import native_fetch_worker as worker
    class Process:
        stdout = io.BytesIO()
        def wait(self, **kwargs):
            return 3221225781
        def poll(self):
            return 3221225781
    monkeypatch.setenv('SystemRoot', 'C:/Windows')
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **kw: Process())
    with pytest.raises(worker.GitCommandError) as caught:
        worker._run(tmp_path / 'git.exe', ['rev-parse', '--verify', 'HEAD'], tmp_path,
                    time.monotonic() + 10, 128)
    assert caught.value.operation == 'rev-parse'
    assert caught.value.code == 3221225781
