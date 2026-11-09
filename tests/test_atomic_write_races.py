"""Concurrent writers cannot grant ownership of unrelated files to atomic_write."""
import os
import stat
import subprocess
import sys

import pytest

from core.secrets import atomic_write


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and chmod race")
def test_atomic_write_does_not_chmod_replacement_of_temporary_file(tmp_path, monkeypatch):
    import tempfile

    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"user data")
    unrelated.chmod(0o644)
    real_mkstemp = tempfile.mkstemp

    def replace_temporary(*args, **kwargs):
        descriptor, temporary = real_mkstemp(*args, **kwargs)
        subprocess.run([
            sys.executable, "-c",
            "import os,sys; os.unlink(sys.argv[1]); os.symlink(sys.argv[2], sys.argv[1])",
            temporary, str(unrelated),
        ], check=True, timeout=10)
        return descriptor, temporary

    def stop_publication(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(tempfile, "mkstemp", replace_temporary)
    monkeypatch.setattr(os, "replace", stop_publication)
    with pytest.raises(OSError, match="injected publication failure"):
        atomic_write(tmp_path / "config.yaml", "new config")
    assert unrelated.read_bytes() == b"user data"
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o644
