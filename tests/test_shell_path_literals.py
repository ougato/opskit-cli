"""PATH entries must remain single literal directories in a real POSIX shell."""
import os
import subprocess

import pytest

from software._shared import shell_path


def test_colon_path_is_rejected_before_profile_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_path.Path, "home", lambda: tmp_path)
    rc = tmp_path / ".profile"
    rc.write_bytes(b"# existing\r\n")
    with pytest.raises(ValueError, match="invalid shim directory"):
        shell_path.inject_rc_path(str(tmp_path / "first:second"), "# begin", "# end")
    assert rc.read_bytes() == b"# existing\r\n"


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX shell")
@pytest.mark.parametrize("suffix", ["with spaces", "quote'path", "$(touch SENTINEL)", "`touch SENTINEL`", "$HOME"])
def test_export_has_one_literal_entry_without_shell_execution(tmp_path, suffix):
    directory = str(tmp_path / suffix)
    result = subprocess.run(
        ["/bin/sh", "-c", shell_path._export_path(directory) + 'printf "%s" "$PATH"'],
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True, timeout=5,
    )
    assert result.stdout.split(":") == [directory, "/usr/bin", "/bin"]
    assert not (tmp_path / "SENTINEL").exists()
