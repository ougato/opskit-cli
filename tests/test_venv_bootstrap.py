"""Bootstrap is testable without installing packages or contacting a network."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import venv_bootstrap as bootstrap


def test_bootstrap_translations_have_matching_keys_and_placeholders():
    import string
    path = Path(bootstrap.__file__).parent / "locale" / "bootstrap.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert catalog["en"].keys() == catalog["zh"].keys()
    formatter = string.Formatter()
    for key in catalog["en"]:
        fields = lambda value: {name for _, name, _, _ in formatter.parse(value) if name}
        assert fields(catalog["en"][key]) == fields(catalog["zh"][key])


@pytest.mark.parametrize(("lang", "expected"), [("en_US.UTF-8", "pip is unavailable"),
                                                 ("zh_CN.UTF-8", "没有可用的 pip")])
def test_missing_pip_has_localized_error_without_download(tmp_path, monkeypatch, lang, expected):
    monkeypatch.setenv("LC_ALL", lang)
    calls = []

    def fail_ensurepip(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return SimpleNamespace(returncode=1)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(bootstrap.subprocess, "run", fail_ensurepip)
    with pytest.raises(RuntimeError, match=expected):
        bootstrap._bootstrap_pip(tmp_path)
    assert [command[2] for command, _ in calls] == ["pip", "ensurepip"]
    assert all(options.get("timeout") for _, options in calls)


def test_pip_bootstrap_requires_no_site_packages(tmp_path):
    project = Path(__file__).resolve().parents[1]
    code = """
from pathlib import Path
from types import SimpleNamespace
from core import venv_bootstrap
def fail(command, **kwargs):
    if kwargs.get('check'):
        raise venv_bootstrap.subprocess.CalledProcessError(1, command)
    return SimpleNamespace(returncode=1)
venv_bootstrap.subprocess.run = fail
try:
    venv_bootstrap._bootstrap_pip(Path('missing'))
except RuntimeError as exc:
    assert 'pip' in str(exc)
else:
    raise AssertionError('missing pip was accepted')
assert 'yaml' not in __import__('sys').modules
"""
    result = subprocess.run([sys.executable, "-S", "-c", code], cwd=project,
                            env=dict(os.environ, LC_ALL="en_US.UTF-8"), capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_find_python_accepts_versioned_python_314_without_aliases(tmp_path, monkeypatch):
    import shutil
    versioned = str(tmp_path / "python3.14")
    monkeypatch.setattr(shutil, "which", lambda name: versioned if name == "python3.14" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="Python 3.14.8\n", stderr=""))
    assert bootstrap._find_python("3.10") == versioned


def test_find_python_uses_current_executable_when_path_has_no_alias(tmp_path, monkeypatch):
    import shutil
    executable = str(tmp_path / "python3.25")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "executable", executable)
    attempted = []

    def which(name):
        attempted.append(name)
        return name if name == executable else None

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="Python 3.25.0\n", stderr=""))
    assert bootstrap._find_python("3.10") == executable
    assert attempted[-1] == executable


def test_find_python_checks_version_instead_of_trusting_executable_name(tmp_path, monkeypatch):
    import shutil
    executable = str(tmp_path / "python3.25")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(shutil, "which", lambda name: str(tmp_path / name) if name == "python3"
                        else executable if name == executable else None)

    def version(command, **kwargs):
        output = "Python 3.9.9\n" if command[0].endswith("python3") else "Python 3.25.0\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", version)
    assert bootstrap._find_python("3.10") == executable


def test_find_python_rejects_failed_version_command(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: "failed-python" if name == "python3.14" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout="Python 3.14.0\n", stderr=""))
    assert bootstrap._find_python("3.10") is None


@pytest.mark.parametrize("returncode,reported_version,accepted", [
    (0, "Python 3.9.25", False),
    (1, "Python 3.14.0", False),
    (0, "Python 3.10.0", True),
    (0, "Python 3.13.5", True),
])
def test_python_install_validates_fallback_interpreter(tmp_path, monkeypatch,
                                                        returncode, reported_version, accepted):
    """A successful package-manager exit does not prove python3 is new enough."""
    import shutil

    commands = []
    tools = {"apt-get": "/fake/apt-get", "python3": "/fake/python3"}
    monkeypatch.setattr(shutil, "which", tools.get)

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "apt-get":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command == ["/fake/python3", "--version"]:
            return SimpleNamespace(returncode=returncode, stdout=reported_version, stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    selected = bootstrap._install_python(tmp_path)
    assert selected == ("/fake/python3" if accepted else None)
    assert ["/fake/python3", "--version"] in commands


def test_failed_setup_is_preserved_and_not_retried_without_proven_ownership(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("example-package\n", encoding="utf-8")
    venv = project / ".venv"
    python = venv / "bin" / "python"
    marker = venv / bootstrap._INCOMPLETE_MARKER
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda path: None)
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)
    pip_available = False
    installs = []

    def run(command, **kwargs):
        if command[1:3] == ["-m", "venv"]:
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            (venv / "pyvenv.cfg").touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1:3] == ["-m", "ensurepip"]:
            raise subprocess.CalledProcessError(1, command)
        if command[1:4] == ["-m", "pip", "--version"]:
            return SimpleNamespace(returncode=0 if pip_available else 1)
        if command[1:4] == ["-m", "pip", "install"]:
            installs.append(command)
            return SimpleNamespace(returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    with pytest.raises(SystemExit) as error:
        bootstrap.ensure_venv(project)
    assert error.value.code == 1
    assert marker.read_text(encoding="utf-8") == bootstrap._MARKER_CONTENT
    assert python.is_file() and entered == []

    pip_available = True
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert installs == [] and entered == []
    assert marker.read_text(encoding="utf-8") == bootstrap._MARKER_CONTENT


def test_existing_directory_with_forged_incomplete_marker_is_untouched(tmp_path, monkeypatch):
    project = tmp_path / "project"
    venv = project / ".venv"
    venv.mkdir(parents=True)
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    user_file = venv / "keep.txt"
    user_file.write_text("user data", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("overwrote user directory"))
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("mutated user directory"))
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert user_file.read_text(encoding="utf-8") == "user data"


@pytest.mark.parametrize("selection", ["prefix", "environment", "executable"])
def test_active_project_environment_cannot_bypass_incomplete_marker(tmp_path, monkeypatch, selection):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.touch()
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "prefix", str(venv if selection == "prefix" else tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(executable if selection == "executable" else tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    if selection == "environment":
        monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_external_virtual_environment_ignores_project_incomplete_marker(tmp_path, monkeypatch):
    project = tmp_path / "project"
    venv = project / ".venv"
    venv.mkdir(parents=True)
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "external"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    bootstrap.ensure_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.parametrize("selection", ["prefix", "environment", "executable"])
def test_windows_project_environment_rejects_incomplete_marker(tmp_path, monkeypatch, selection):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "prefix", str(venv if selection == "prefix" else tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    if selection == "environment":
        monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered incomplete environment"))
    with pytest.raises(SystemExit):
        bootstrap.reenter_existing_windows_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_windows_external_environment_ignores_project_marker(tmp_path, monkeypatch):
    project = tmp_path / "project"
    venv = project / ".venv"
    venv.mkdir(parents=True)
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "external"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    bootstrap.reenter_existing_windows_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.parametrize("marker_content", [None, bootstrap._COMPLETE_MARKER_CONTENT])
def test_windows_existing_environment_is_reused(tmp_path, monkeypatch, marker_content):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    if marker_content is not None:
        (venv / bootstrap._INCOMPLETE_MARKER).write_bytes(marker_content.encode())
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)
    bootstrap.reenter_existing_windows_venv(project)
    assert entered == [executable]


def test_new_directory_identity_change_is_detected_before_completion(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered changed directory"))
    original_lstat = Path.lstat

    def change_identity(*args):
        def changed(path, *a, **kw):
            info = original_lstat(path, *a, **kw)
            if path == venv:
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino + 1)
            return info
        monkeypatch.setattr(Path, "lstat", changed)

    monkeypatch.setattr(bootstrap, "_create_venv", change_identity)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_new_marker_replacement_preserves_unrelated_file(tmp_path, monkeypatch):
    """A content match alone cannot give ownership of a substituted marker."""
    from contextlib import contextmanager

    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    user_file = project / "user-file"
    original_content = bootstrap._MARKER_CONTENT.encode()
    user_file.write_bytes(original_content)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("used substituted marker"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered substituted marker"))
    original_hold = bootstrap._hold_setup_marker

    @contextmanager
    def replace_before_open(venv_dir, **kwargs):
        path = venv_dir / bootstrap._INCOMPLETE_MARKER
        path.rename(project / "original-marker")
        user_file.rename(path)
        with original_hold(venv_dir, **kwargs) as result:
            yield result

    monkeypatch.setattr(bootstrap, "_hold_setup_marker", replace_before_open)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == original_content
    if os.name == "nt":
        # The open Windows handle prevents renaming the original marker.
        assert user_file.read_bytes() == original_content
    else:
        # POSIX allows the rename, but the open handle detects the replacement.
        assert (project / "original-marker").read_bytes() == original_content


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits renaming the open setup directory")
def test_replaced_environment_is_not_used_for_dependency_install(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    victim = project / "user-directory"
    victim.mkdir()
    (victim / "keep.txt").write_bytes(b"user data")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("used replaced environment"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered replaced environment"))

    (victim / "bin").mkdir()
    (victim / "bin" / "python").touch()
    (victim / "pyvenv.cfg").touch()
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda *_: None)

    def replace_during_creation(*args, **kwargs):
        venv.rename(project / "original-venv")
        victim.rename(venv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", replace_during_creation)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / "keep.txt").read_bytes() == b"user data"
    assert (project / "original-venv" / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits renaming an open marker")
def test_replaced_marker_prevents_dependency_install_and_completion(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    marker = venv / bootstrap._INCOMPLETE_MARKER
    replacement = project / "user-file"
    replacement.write_bytes(b"user data")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("used replaced marker"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered replaced marker"))

    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda *_: None)

    def replace_during_creation(*args, **kwargs):
        (venv / "bin").mkdir()
        (venv / "bin" / "python").touch()
        (venv / "pyvenv.cfg").touch()
        marker.rename(project / "original-marker")
        replacement.rename(marker)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", replace_during_creation)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert marker.read_bytes() == b"user data"
    assert (project / "original-marker").read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-relative file creation")
def test_new_marker_uses_opened_directory_after_path_replacement(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    victim = project / "user-directory"
    victim.mkdir()
    user_file = victim / "keep.txt"
    user_file.write_bytes(b"user data")
    original_dir = project / "original-venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("used substituted directory"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered substituted directory"))
    original_open = bootstrap._open_new_marker

    def replace_before_marker(path, directory_fd):
        assert directory_fd is not None
        venv.rename(original_dir)
        victim.rename(venv)
        return original_open(path, directory_fd)

    monkeypatch.setattr(bootstrap, "_open_new_marker", replace_before_marker)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / "keep.txt").read_bytes() == b"user data"
    assert not (venv / bootstrap._INCOMPLETE_MARKER).exists()
    assert (original_dir / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_new_directory_without_inode_is_not_used_for_setup(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("used unknown directory identity"))
    original_lstat = Path.lstat

    def missing_inode(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == venv:
            return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=0)
        return info

    monkeypatch.setattr(Path, "lstat", missing_inode)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert not (venv / bootstrap._INCOMPLETE_MARKER).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
def test_new_environment_rejects_already_injected_hardlink(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    victim = project / "user-config"
    original = b"user-owned configuration\n"
    victim.write_bytes(original)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("used an unknown entry"))
    check = bootstrap._check_new_directory_contents

    def inject_before_check(venv_dir, directory_fd):
        os.link(victim, venv_dir / "pyvenv.cfg")
        check(venv_dir, directory_fd)

    monkeypatch.setattr(bootstrap, "_check_new_directory_contents", inject_before_check)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert victim.read_bytes() == original


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A17: venv follows the target path after the identity check")
def test_new_environment_replacement_cannot_overwrite_user_data(tmp_path, monkeypatch):
    """A real venv must not write into a replacement directory, even temporarily."""
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    victim = project / "user-directory"
    victim.mkdir()
    original = b"user-owned configuration\n"
    (victim / "pyvenv.cfg").write_bytes(original)
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: real_python)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("unexpected reentry"))

    def replace_before_create(python_bin, venv_dir):
        venv_dir.rename(project / "original-venv")
        victim.rename(venv_dir)
        subprocess.run([python_bin, "-m", "venv", "--without-pip", str(venv_dir)],
                       check=True, capture_output=True, timeout=30)

    monkeypatch.setattr(bootstrap, "_create_venv", replace_before_create)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / "pyvenv.cfg").read_bytes() == original


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A17: venv follows hard-linked configuration files")
def test_new_environment_hardlink_cannot_overwrite_user_data(tmp_path, monkeypatch):
    """A real venv must not truncate an unrelated file through a hard link."""
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    victim = project / "user-config"
    original = b"user-owned configuration\n"
    victim.write_bytes(original)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: real_python)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: None)

    def create_with_link(python_bin, venv_dir):
        os.link(victim, venv_dir / "pyvenv.cfg")
        subprocess.run([python_bin, "-m", "venv", "--without-pip", str(venv_dir)],
                       check=True, capture_output=True, timeout=30)

    monkeypatch.setattr(bootstrap, "_create_venv", create_with_link)
    bootstrap.ensure_venv(project)
    assert victim.read_bytes() == original


def test_successful_venv_command_without_interpreter_does_not_install(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda path: None)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("installed without interpreter"))
    with pytest.raises(SystemExit):
        bootstrap._create_venv("python", tmp_path / ".venv")


def test_concurrent_resume_does_not_install_twice(tmp_path, monkeypatch):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (venv / bootstrap._INCOMPLETE_MARKER).write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("duplicate installation"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("premature reentry"))
    with bootstrap._hold_setup_marker(venv):
        with pytest.raises(SystemExit):
            bootstrap.ensure_venv(project)


def test_existing_incomplete_user_directory_is_preserved(tmp_path, monkeypatch):
    project = tmp_path / "project"
    directory = project / ".venv"
    directory.mkdir(parents=True)
    user_file = directory / "keep.txt"
    user_file.write_text("user data", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("overwrote user directory"))
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert user_file.read_text(encoding="utf-8") == "user data"


def test_sibling_directory_name_does_not_count_as_project_venv(tmp_path, monkeypatch):
    project = tmp_path / "project"
    expected = project / ".venv" / "bin" / "python"
    expected.parent.mkdir(parents=True)
    expected.touch()
    sibling = project / ".venv-other" / "bin" / "python"
    sibling.parent.mkdir(parents=True)
    sibling.touch()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(sibling))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)
    bootstrap.ensure_venv(project)
    assert entered == [expected]
