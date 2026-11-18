from pathlib import Path
from core import venv_bootstrap as bootstrap
import pytest


def test_existing_project_environment_is_reused(tmp_path, monkeypatch):
    executable = tmp_path / '.venv/Scripts/python.exe'
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(bootstrap.sys, 'prefix', 'base')
    monkeypatch.setattr(bootstrap.sys, 'base_prefix', 'base')
    monkeypatch.delenv('VIRTUAL_ENV', raising=False)
    seen = []
    monkeypatch.setattr(bootstrap, '_exec_reenter', seen.append)
    bootstrap.reenter_existing_windows_venv(tmp_path)
    assert seen == [executable]


def test_explicit_environment_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.sys, 'prefix', 'chosen-venv')
    monkeypatch.setattr(bootstrap.sys, 'base_prefix', 'base')
    monkeypatch.setattr(bootstrap, '_exec_reenter', lambda *a: pytest.fail('changed explicit environment'))
    bootstrap.reenter_existing_windows_venv(tmp_path)


def test_windows_reentry_preserves_wait_and_exit_code(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'win32')
    monkeypatch.setattr(bootstrap.sys, 'argv', ['main.py', 'plugin', 'list'])
    seen = []
    monkeypatch.setattr(bootstrap.subprocess, 'call', lambda args: seen.append(args) or 7)
    with pytest.raises(SystemExit) as error:
        bootstrap._exec_reenter(Path('env/python.exe'))
    assert error.value.code == 7
    assert seen == [[str(Path('env/python.exe')), 'main.py', 'plugin', 'list']]


def test_windows_reentry_parent_handles_ctrl_c_without_stopping_child(monkeypatch):
    import signal

    monkeypatch.setattr(bootstrap.sys, 'platform', 'win32')
    previous = signal.getsignal(signal.SIGINT)
    def child(args):
        assert callable(signal.getsignal(signal.SIGINT))
        signal.raise_signal(signal.SIGINT)
        return 7
    monkeypatch.setattr(bootstrap.subprocess, 'call', child)
    with pytest.raises(SystemExit) as error:
        bootstrap._exec_reenter(Path('env/python.exe'))
    assert error.value.code == 7
    assert signal.getsignal(signal.SIGINT) == previous


def test_windows_reentry_unexpected_interrupt_has_no_traceback(monkeypatch):
    import signal

    monkeypatch.setattr(bootstrap.sys, 'platform', 'win32')
    previous = signal.getsignal(signal.SIGINT)
    def interrupted(args):
        raise KeyboardInterrupt
    monkeypatch.setattr(bootstrap.subprocess, 'call', interrupted)
    with pytest.raises(SystemExit) as error:
        bootstrap._exec_reenter(Path('env/python.exe'))
    assert error.value.code == 130
    assert signal.getsignal(signal.SIGINT) == previous


def test_missing_transport_dependency_is_not_reported_as_network_failure(monkeypatch):
    import tempfile
    from types import SimpleNamespace
    from core import native_git_https, private_file
    from network import worker
    results = []
    monkeypatch.setattr(worker, 'send', lambda connection, message: results.append(message))
    monkeypatch.setattr(private_file, 'verify_private_file', lambda descriptor: None)
    def missing(*args):
        raise ModuleNotFoundError('paramiko')
    monkeypatch.setattr(native_git_https, '_download', missing)
    with tempfile.TemporaryFile() as output:
        native_git_https._worker(SimpleNamespace(close=lambda: None), 'source', None, False, output, None)
    assert results == [{'error': 'fetch_dependency_missing'}]
