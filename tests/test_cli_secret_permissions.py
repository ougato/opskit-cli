"""Native ACL/mode fixtures are temporary; input checks never change them."""
import os
import subprocess
import sys

import pytest

from core.cli_secrets import read_secret_file
from core.secrets import restrict_permissions


def private_input(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"synthetic-permission-fixture")
    restrict_permissions(path)
    return path


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode contract")
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620], ids=["group-read", "other-read", "group-write"])
def test_posix_shared_input_refused_without_chmod(tmp_path, mode):
    path = private_input(tmp_path)
    path.chmod(mode)
    before = path.stat().st_mode
    with pytest.raises(ValueError, match="owner-only"):
        read_secret_file(str(path))
    assert path.stat().st_mode == before


@pytest.mark.skipif(os.name != "nt", reason="native Windows DACL")
def test_windows_everyone_read_acl_refused(tmp_path):
    path = private_input(tmp_path)
    subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:R"],
                   check=True, capture_output=True, timeout=10)
    with pytest.raises(ValueError, match="another identity"):
        read_secret_file(str(path))


@pytest.mark.skipif(os.name != "nt", reason="native Windows DACL")
def test_windows_acl_grant_during_read_refused(tmp_path, monkeypatch):
    path = private_input(tmp_path)
    real = os.fdopen
    class ChangedAcl:
        def __init__(self, *args, **kwargs):
            self.stream = real(*args, **kwargs)
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def read(self, count):
            result = self.stream.read(count)
            subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:R"],
                           check=True, capture_output=True, timeout=10)
            return result
    monkeypatch.setattr(os, "fdopen", ChangedAcl)
    with pytest.raises(ValueError, match="another identity"):
        read_secret_file(str(path))


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin extended ACL")
def test_darwin_extra_acl_refused_with_private_mode(tmp_path):
    path = private_input(tmp_path)
    subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(path)],
                   check=True, capture_output=True, timeout=10)
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="extended ACL"):
        read_secret_file(str(path))
