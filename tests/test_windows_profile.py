"""PowerShell profile publication must preserve unrelated content and failures."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from software._shared import windows_profile


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return windows_profile._profiles()


@pytest.mark.parametrize("bom", [False, True])
def test_idempotence_and_removal_preserve_unrelated_crlf(profiles, tmp_path, bom):
    original = (b"\xef\xbb\xbf" if bom else b"") + b"# administrator\r\n"
    for path in profiles:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)
    args = (str(tmp_path / "quote'path"), "# begin", "# end")
    windows_profile.inject_profile(*args)
    first = [path.read_bytes() for path in profiles]
    windows_profile.inject_profile(*args)
    assert [path.read_bytes() for path in profiles] == first
    assert "quote''path" in profiles[0].read_text(encoding="utf-8")
    windows_profile.remove_profile("# begin", "# end")
    assert [path.read_bytes() for path in profiles] == [original] * 3


@pytest.mark.parametrize("existing", [False, True])
def test_late_publication_failure_restores_files_and_new_directories(profiles, tmp_path, monkeypatch, existing):
    if existing:
        for path in profiles:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# administrator\r\n")
    original = os.replace
    def denied(source, target):
        if target == profiles[-1]:
            raise PermissionError("late profile failure")
        return original(source, target)
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        windows_profile.inject_profile(str(tmp_path / "shims"), "# begin", "# end")
    if existing:
        assert [path.read_bytes() for path in profiles] == [b"# administrator\r\n"] * 3
    else:
        assert not (tmp_path / "Documents").exists()


def test_ambiguous_late_marker_refused_before_any_file_change(profiles, tmp_path):
    for path in profiles:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# administrator\r\n")
    profiles[-1].write_text("# begin\n# preserve tail\n")
    before = [path.read_bytes() for path in profiles]
    with pytest.raises(ValueError, match="ambiguous"):
        windows_profile.remove_profile("# begin", "# end")
    assert [path.read_bytes() for path in profiles] == before


def test_path_delimiter_refused_before_creating_profiles(profiles, tmp_path):
    with pytest.raises(ValueError, match="invalid PowerShell"):
        windows_profile.inject_profile(str(tmp_path / "first;second"), "# begin", "# end")
    assert not (tmp_path / "Documents").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows PowerShell")
@pytest.mark.parametrize("interpreter", ["powershell", "pwsh"])
@pytest.mark.parametrize("suffix", ["quote' & $(New-Item SENTINEL)", "中文' & $(New-Item SENTINEL)"])
def test_native_powershell_keeps_path_literal(profiles, tmp_path, interpreter, suffix):
    executable = shutil.which(interpreter)
    if executable is None and interpreter == "powershell":
        candidate = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        pytest.skip("Windows PowerShell unavailable")
    directory = str(tmp_path / suffix)
    windows_profile.inject_profile(directory, "# begin", "# end")
    probe = tmp_path / "probe.ps1"
    probe.write_bytes(b'$opskitBaseline = $env:PATH\n' + profiles[0].read_bytes() +
                      b'[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n'
                      b'[Console]::Write($opskitBaseline + [char]0 + $env:PATH)\n')
    environment = dict(os.environ, PATH="baseline")
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
                            cwd=tmp_path, env=environment, capture_output=True, encoding="utf-8", timeout=20, check=True)
    before, after = result.stdout.split("\x00")
    assert before.endswith("baseline")
    assert after == directory + ";" + before
    assert not (tmp_path / "SENTINEL").exists()
