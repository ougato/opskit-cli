"""One Windows account authority must survive different caller home hints."""
import os
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from software._shared import mutation
from software.base import InstallError


@pytest.mark.skipif(os.name != "nt", reason="requires Windows known-folder API")
def test_native_account_directory_ignores_environment_home_hints(tmp_path, monkeypatch):
    from software._shared.windows_account import local_app_data
    first = local_app_data()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "other-local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "other-user"))
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "other-data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "other-home")
    assert local_app_data() == first
    source = "from software._shared.windows_account import local_app_data; import hashlib; print(hashlib.sha256(str(local_app_data()).encode('utf-8')).hexdigest())"
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, timeout=20, check=True)
    assert result.stdout.strip() == hashlib.sha256(str(first).encode("utf-8")).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows account lock routing")
@pytest.mark.timeout(30)
def test_other_home_process_is_denied_then_releases(tmp_path, monkeypatch):
    from software._shared import windows_account
    monkeypatch.undo()
    authority = tmp_path / "account-authority"
    monkeypatch.setattr(windows_account, "local_app_data", lambda: authority)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "parent-home")
    source = '''
import sys
from pathlib import Path
from software._shared import mutation, windows_account
windows_account.local_app_data = lambda: Path(sys.argv[1])
Path.home = lambda: Path(sys.argv[2])
with mutation.mutation_lock():
    print('ready', flush=True)
    sys.stdin.read()
'''
    child = subprocess.Popen([sys.executable, "-u", "-c", source, str(authority), str(tmp_path / "child-home")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(InstallError, match="busy"):
            with mutation.mutation_lock():
                pytest.fail("different home bypassed account lock")
        child.communicate(input="", timeout=5)
        assert child.returncode == 0
        with mutation.mutation_lock(), mutation.mutation_lock():
            assert mutation.lock_path().is_relative_to(authority)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
