"""System writes share one lock independently of each account's home."""
import subprocess
import sys

import pytest

from software._shared import mutation, shell_path
from software.base import InstallError


def test_all_versioned_lifecycles_reject_system_contention_before_home_lock(monkeypatch):
    from contextlib import contextmanager
    from software.registry import get

    monkeypatch.setattr(mutation, "_system_privileged", lambda: True)
    @contextmanager
    def busy():
        raise InstallError("system software is busy")
        yield

    def unexpected_home_lock():
        raise AssertionError("account state reached before system authority")

    monkeypatch.setattr(mutation, "system_mutation_lock", busy)
    monkeypatch.setattr(mutation, "mutation_lock", unexpected_home_lock)
    for key in ('python', 'nodejs', 'golang', 'java', 'mongodb', 'mysql', 'postgresql', 'redis', 'clickhouse'):
        recipe = get(key)()
        for method in ('install', 'upgrade', 'switch', 'uninstall'):
            with pytest.raises(InstallError, match="system software is busy"):
                getattr(recipe, method)("not-installed")


@pytest.mark.timeout(30)
@pytest.mark.parametrize("operation,args", [
    (shell_path.inject_rc_path, ("/unused", "# begin", "# end")),
    (shell_path.remove_rc_path, ("# begin", "# end")),
    (shell_path.link_into_system_bin, ("/unused", [])),
    (shell_path.unlink_system_bin, ([], "/unused")),
])
def test_shared_entry_denied_before_filesystem_changes(operation, args, monkeypatch):
    monkeypatch.setattr(mutation, "_system_privileged", lambda: True)
    path = mutation.system_lock_path()
    source = '''
import sys
from pathlib import Path
from software._shared import mutation
mutation.system_lock_path = lambda: Path(sys.argv[1])
with mutation.system_mutation_lock():
    print("ready", flush=True)
    sys.stdin.read()
'''
    child = subprocess.Popen([sys.executable, "-u", "-c", source, str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        kwargs = {"managed_root": "/unused"} if operation is shell_path.link_into_system_bin else {}
        with pytest.raises(InstallError, match="busy"):
            operation(*args, **kwargs)
        child.communicate(input="", timeout=5)
        assert child.returncode == 0
        with mutation.system_mutation_lock(), mutation.system_mutation_lock():
            pass
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_nonprivileged_entry_does_not_acquire_system_lock(monkeypatch):
    monkeypatch.setattr(mutation, "_system_privileged", lambda: False)
    monkeypatch.setattr(shell_path, "_is_root", lambda: False)
    def unexpected():
        raise AssertionError("nonprivileged caller acquired system lock")
    monkeypatch.setattr(mutation, "system_mutation_lock", unexpected)
    shell_path.link_into_system_bin("/unused", [], managed_root="/unused")
