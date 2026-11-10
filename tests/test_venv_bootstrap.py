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


@pytest.mark.parametrize(("lang", "expected", "recovery"), [
    ("en_US.UTF-8", "pip is unavailable", "inspect and resolve the incomplete environment"),
    ("zh_CN.UTF-8", "没有可用的 pip", "检查并处理未完成的虚拟环境"),
])
def test_missing_pip_has_localized_error_without_download(tmp_path, monkeypatch, lang, expected, recovery):
    monkeypatch.setenv("LC_ALL", lang)
    calls = []

    def fail_ensurepip(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return SimpleNamespace(returncode=1)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(bootstrap.subprocess, "run", fail_ensurepip)
    with pytest.raises(RuntimeError, match=expected) as error:
        bootstrap._bootstrap_pip(tmp_path)
    assert recovery in str(error.value)
    assert [command[1:4] for command, _ in calls] == [["-I", "-m", "pip"], ["-I", "-m", "ensurepip"]]
    assert all(options.get("timeout") for _, options in calls)


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
def test_pip_bootstrap_ignores_ambient_python_modules_and_home(tmp_path, monkeypatch):
    clean_env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    check = subprocess.run([sys.executable, "-m", "ensurepip", "--version"], env=clean_env,
                           capture_output=True, timeout=15)
    if check.returncode:
        pytest.skip("interpreter has no bundled ensurepip")
    venv = tmp_path / "env"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], env=clean_env,
                   check=True, capture_output=True, timeout=60)
    ambient_pip = tmp_path / "ambient" / "pip"
    ambient_pip.mkdir(parents=True)
    (ambient_pip / "__init__.py").write_text("", encoding="utf-8")
    (ambient_pip / "__main__.py").write_text("print('ambient pip')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(ambient_pip.parent))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "nonexistent-home"))
    monkeypatch.setenv("PIP_NO_INDEX", "1")

    bootstrap._bootstrap_pip(venv)

    pip = venv / "bin" / "pip"
    assert pip.is_file()
    actual = subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "--version"],
                            env=bootstrap._python_setup_env(), capture_output=True, text=True, check=True)
    assert str(venv) in actual.stdout and "ambient pip" not in actual.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
def test_first_setup_ignores_cwd_modules_but_keeps_pip_config_and_relative_requirements(tmp_path, monkeypatch):
    import zipfile

    clean_env = bootstrap._python_setup_env()
    if subprocess.run([sys.executable, "-I", "-m", "ensurepip", "--version"], env=clean_env,
                      capture_output=True, timeout=15).returncode:
        pytest.skip("interpreter has no bundled ensurepip")
    project = tmp_path / "project"
    project.mkdir()
    sentinel = project / "shadow-executed"
    for name in ("venv", "ensurepip", "pip"):
        package = project / name
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            "from pathlib import Path\nPath('shadow-executed').write_text('incorrect import')\n",
            encoding="utf-8")
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    dist = "opskit_smoke_sample-1.0.dist-info"
    files = {
        "opskit_smoke_sample/__init__.py": b"value = 42\n",
        f"{dist}/METADATA": b"Metadata-Version: 2.1\nName: opskit-smoke-sample\nVersion: 1.0\n",
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files[f"{dist}/RECORD"] = "".join(f"{name},,\n" for name in (*files, f"{dist}/RECORD")).encode()
    with zipfile.ZipFile(wheelhouse / "opskit_smoke_sample-1.0-py3-none-any.whl", "w") as wheel:
        for name, content in files.items():
            wheel.writestr(name, content)
    config = tmp_path / "pip.conf"
    config.write_text(f"[global]\nno-index = true\nfind-links = {wheelhouse}\n", encoding="utf-8")
    nested = project / "requirements"
    nested.mkdir()
    (project / "requirements.txt").write_text("-r requirements/extra.txt\n", encoding="utf-8")
    (nested / "extra.txt").write_text("opskit-smoke-sample==1.0\n", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PIP_CONFIG_FILE", str(config))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda _: sys.executable)
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)

    bootstrap.ensure_venv(project)

    venv = project / ".venv"
    assert entered == [venv / "bin" / "python"]
    assert (venv / "bin" / "pip").is_file()
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._COMPLETE_MARKER_CONTENT.encode()
    result = subprocess.run([str(venv / "bin" / "python"), "-I", "-c",
                             "import opskit_smoke_sample; assert opskit_smoke_sample.value == 42"],
                            env=bootstrap._python_setup_env(), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()


def test_dependency_install_ignores_ambient_python_paths(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONHOME", "outside-home")
    monkeypatch.setenv("PYTHONPATH", "outside-modules")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setattr(bootstrap, "_bootstrap_pip", lambda *_: None)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    bootstrap._install_dependencies(tmp_path / "env", project)
    assert len(calls) == 1
    env = calls[0][1]["env"]
    assert "PYTHONHOME" not in env and "PYTHONPATH" not in env
    assert env["PIP_NO_INDEX"] == "1"
    assert os.environ["PYTHONHOME"] == "outside-home" and os.environ["PYTHONPATH"] == "outside-modules"


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
def test_pip_bootstrap_directory_swap_does_not_install_into_replacement(tmp_path, monkeypatch):
    """An actual child replaces the directory as the real pip probe completes."""
    clean_env = bootstrap._python_setup_env()
    if subprocess.run([sys.executable, "-m", "ensurepip", "--version"], env=clean_env,
                      capture_output=True, timeout=15).returncode:
        pytest.skip("interpreter has no bundled ensurepip")
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    venv, original, victim = project / ".venv", project / "held-venv", project / "victim"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   env=clean_env, check=True, capture_output=True, timeout=60)
    marker_path = venv / bootstrap._INCOMPLETE_MARKER
    marker_path.write_bytes(bootstrap._MARKER_CONTENT.encode())
    victim.mkdir()
    (victim / bootstrap._INCOMPLETE_MARKER).write_bytes(bootstrap._MARKER_CONTENT.encode())
    (victim / "user-data").write_bytes(b"preserve this user file")
    opened_dir = venv.lstat()
    real_run = subprocess.run
    swaps = []

    def run(command, **options):
        if command[1:5] == ["-I", "-m", "pip", "install"]:
            pytest.fail("pip install used the substituted environment")
        result = real_run(command, **options)
        if command[1:5] == ["-I", "-m", "pip", "--version"] and options.get("check"):
            real_run([sys.executable, "-c",
                      "import os,sys; os.rename(sys.argv[1],sys.argv[2]); os.rename(sys.argv[3],sys.argv[1])",
                      str(venv), str(original), str(victim)], env=clean_env, check=True, timeout=15)
            swaps.append(True)
        return result

    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    with marker_path.open("r+b") as marker, pytest.raises(SystemExit):
        bootstrap._install_dependencies(venv, project, opened_dir, marker)
    assert swaps == [True]
    assert (venv / "user-data").read_bytes() == b"preserve this user file"
    assert (original / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_system_ensurepip_probe_ignores_ambient_python_paths(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "outside-home")
    monkeypatch.setenv("PYTHONPATH", "outside-modules")

    def probe(command, **kwargs):
        assert command[1:] == ["-I", "-m", "ensurepip", "--version"]
        assert "PYTHONHOME" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap.subprocess, "run", probe)
    bootstrap._ensure_venv_pkg("system-python")


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
        if command[1:4] == ["-I", "-m", "venv"]:
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            (venv / "pyvenv.cfg").touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1:4] == ["-I", "-m", "ensurepip"]:
            raise subprocess.CalledProcessError(1, command)
        if command[1:5] == ["-I", "-m", "pip", "--version"]:
            return SimpleNamespace(returncode=0 if pip_available else 1)
        if command[1:5] == ["-I", "-m", "pip", "install"]:
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


def test_completion_fsync_failure_leaves_incomplete_marker_and_blocks_reentry(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    venv = project / ".venv"
    marker = venv / bootstrap._INCOMPLETE_MARKER
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)

    def create(*args):
        executable = venv / "bin" / "python"
        executable.parent.mkdir()
        executable.touch()

    monkeypatch.setattr(bootstrap, "_create_venv", create)
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: None)
    real_fsync = os.fsync
    synced = []

    def fail_completion(fd):
        from core.file_identity import same_file_identity
        if marker.exists() and same_file_identity(os.fstat(fd), marker.stat()):
            synced.append(fd)
            if len(synced) == 2:
                raise OSError("injected completion fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(bootstrap.os, "fsync", fail_completion)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert len(synced) >= 3
    assert entered == []

    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert entered == []


@pytest.mark.parametrize("failing_operation", ["write", "short_write", "truncate", "flush"])
def test_completion_write_failure_restores_marker_using_held_file(tmp_path, failing_operation):
    marker_path = tmp_path / bootstrap._INCOMPLETE_MARKER
    marker_path.write_bytes(bootstrap._MARKER_CONTENT.encode())

    class FailingMarker:
        def __init__(self, file):
            self.file = file
            self.failed = False

        def __getattr__(self, name):
            method = getattr(self.file, name)

            def call(*args):
                if not self.failed and name == failing_operation:
                    self.failed = True
                    method(*args)
                    raise OSError("injected marker failure")
                if not self.failed and name == "write" and failing_operation == "short_write":
                    self.failed = True
                    method(args[0][:3])
                    return 3
                return method(*args)

            return call if callable(method) else method

    with marker_path.open("r+b") as file:
        with pytest.raises(OSError):
            bootstrap._complete_setup(FailingMarker(file))
    assert marker_path.read_bytes() == bootstrap._MARKER_CONTENT.encode()
    with pytest.raises(SystemExit):
        bootstrap._check_existing_marker(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows keeps the open marker from being renamed")
def test_completion_failure_restores_open_marker_not_replacement(tmp_path, monkeypatch):
    marker = tmp_path / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    original = tmp_path / "original-marker"
    real_fsync = os.fsync
    first = True

    def replace_and_fail(fd):
        nonlocal first
        if first:
            first = False
            marker.rename(original)
            marker.write_bytes(b"unrelated user data")
            raise OSError("injected completion fsync failure")
        return real_fsync(fd)

    with bootstrap._hold_setup_marker(tmp_path) as (file, _):
        monkeypatch.setattr(bootstrap.os, "fsync", replace_and_fail)
        with pytest.raises(OSError):
            bootstrap._complete_setup(file)
    assert original.read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert marker.read_bytes() == b"unrelated user data"


def test_completion_restoration_failure_reports_uncertain_marker(tmp_path, monkeypatch):
    marker = tmp_path / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())

    class RestoreFailure:
        def __init__(self, file):
            self.file = file
            self.writes = 0

        def write(self, data):
            self.writes += 1
            if self.writes == 2:
                raise OSError("injected restoration write failure")
            return self.file.write(data)

        def __getattr__(self, name):
            return getattr(self.file, name)

    def fail_sync(_):
        raise OSError("injected completion sync failure")

    monkeypatch.setattr(bootstrap.os, "fsync", fail_sync)
    with marker.open("r+b") as file:
        with pytest.raises(RuntimeError, match="marker restoration failed"):
            bootstrap._complete_setup(RestoreFailure(file))
    # The first write may already be visible; a second I/O failure means this
    # mechanism cannot promise that a later process will reject the marker.
    assert marker.read_bytes() == bootstrap._COMPLETE_MARKER_CONTENT.encode()


def test_completion_interrupt_restores_incomplete_marker(tmp_path, monkeypatch):
    marker = tmp_path / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    real_fsync = os.fsync
    first = True

    def interrupt_once(fd):
        nonlocal first
        if first:
            first = False
            raise KeyboardInterrupt
        return real_fsync(fd)

    monkeypatch.setattr(bootstrap.os, "fsync", interrupt_once)
    with marker.open("r+b") as file:
        with pytest.raises(KeyboardInterrupt):
            bootstrap._complete_setup(file)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


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


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("prefix_is_external", [False, True])
def test_project_executable_checks_marker_before_conflicting_environment(tmp_path, monkeypatch, windows,
                                                                           prefix_is_external):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / ("Scripts/python.exe" if windows else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ("external" if prefix_is_external else "base")))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "external"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered incomplete environment"))

    with pytest.raises(SystemExit):
        (bootstrap.reenter_existing_windows_venv if windows else bootstrap.ensure_venv)(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("marker_content", [None, bootstrap._COMPLETE_MARKER_CONTENT])
def test_project_executable_keeps_prepared_and_legacy_environment_compatibility(tmp_path, monkeypatch,
                                                                                windows, marker_content):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / ("Scripts/python.exe" if windows else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    marker = venv / bootstrap._INCOMPLETE_MARKER
    if marker_content is not None:
        marker.write_bytes(marker_content.encode())
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "external"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("unexpected reentry"))

    (bootstrap.reenter_existing_windows_venv if windows else bootstrap.ensure_venv)(project)
    assert marker.exists() is (marker_content is not None)
    if marker_content is not None:
        assert marker.read_bytes() == marker_content.encode()


@pytest.mark.parametrize("windows", [False, True])
def test_project_directory_alias_checks_incomplete_marker(tmp_path, monkeypatch, windows):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / ("Scripts/python.exe" if windows else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    alias = tmp_path / "project-alias"
    try:
        alias.symlink_to(project, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlink unavailable")
        junction = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(project)],
                                  capture_output=True, timeout=15)
        if junction.returncode:
            pytest.skip("directory alias unavailable")
    monkeypatch.setattr(sys, "executable", str(alias / executable.relative_to(project)))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "external"))

    with pytest.raises(SystemExit):
        (bootstrap.reenter_existing_windows_venv if windows else bootstrap.ensure_venv)(project)
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_actual_project_python_with_no_site_and_other_virtual_env_preserves_incomplete_marker(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    venv = project / ".venv"
    clean_env = bootstrap._python_setup_env()
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   env=clean_env, check=True, capture_output=True, timeout=60)
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(bootstrap._MARKER_CONTENT.encode())
    executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    repository = Path(bootstrap.__file__).resolve().parents[1]
    code = ("import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(repository)!r}); "
            "from core import venv_bootstrap as b; "
            "f = b.reenter_existing_windows_venv if sys.platform == 'win32' else b.ensure_venv; "
            "f(Path(sys.argv[1])); print('unexpected-return')")
    clean_env["VIRTUAL_ENV"] = str(tmp_path / "external")
    result = subprocess.run([str(executable), "-S", "-c", code, str(project)],
                            env=clean_env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "unexpected-return" not in result.stdout
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("witness_kind", ["file", "symlink"])
def test_missing_primary_marker_with_witness_refuses_reentry(tmp_path, monkeypatch, windows, witness_kind):
    project = tmp_path / "project"
    venv = project / ".venv"
    executable = venv / ("Scripts/python.exe" if windows else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    witness = venv / ".opskit-bootstrap-witness"
    external = project / "user-file"
    external.write_bytes(b"preserve")
    if witness_kind == "symlink":
        try:
            witness.symlink_to(external)
        except OSError:
            pytest.skip("symbolic links unavailable")
    else:
        witness.write_bytes(b"witness")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered an unproven environment"))
    with pytest.raises(SystemExit):
        (bootstrap.reenter_existing_windows_venv if windows else bootstrap.ensure_venv)(project)
    assert external.read_bytes() == b"preserve"
    assert witness.exists() or witness.is_symlink()


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


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("marker_content", [bootstrap._MARKER_CONTENT, bootstrap._COMPLETE_MARKER_CONTENT])
def test_marker_without_interpreter_never_falls_back_or_reenters(tmp_path, monkeypatch, windows, marker_content):
    project = tmp_path / "project"
    venv = project / ".venv"
    venv.mkdir(parents=True)
    marker = venv / bootstrap._INCOMPLETE_MARKER
    marker.write_bytes(marker_content.encode())
    user_file = venv / "keep.txt"
    user_file.write_text("user data", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered without an interpreter"))
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("modified an existing environment"))

    with pytest.raises(SystemExit) as error:
        (bootstrap.reenter_existing_windows_venv if windows else bootstrap.ensure_venv)(project)

    assert error.value.code == 1
    assert marker.read_bytes() == marker_content.encode()
    assert user_file.read_text(encoding="utf-8") == "user data"


def test_marker_sync_failure_stops_before_creating_interpreter(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("created interpreter after failed sync"))

    marker = project / ".venv" / bootstrap._INCOMPLETE_MARKER
    real_fsync = os.fsync

    def fail_sync(descriptor):
        from core.file_identity import same_file_identity
        if marker.exists() and same_file_identity(os.fstat(descriptor), marker.stat()):
            raise OSError("marker sync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr(bootstrap.os, "fsync", fail_sync)
    with pytest.raises(SystemExit) as error:
        bootstrap.ensure_venv(project)
    assert error.value.code == 1
    assert marker.read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert not (project / ".venv" / "bin" / "python").exists()


def test_new_setup_persists_witness_and_keeps_v1_marker(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_bytes(b"")
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)

    def create(*args):
        assert (venv / bootstrap._SETUP_WITNESS).read_bytes() == bootstrap._WITNESS_CONTENT.encode()
        args[3].seek(0)
        assert args[3].read() == bootstrap._MARKER_CONTENT.encode()
        executable = venv / "bin" / "python"
        executable.parent.mkdir()
        executable.touch()

    monkeypatch.setattr(bootstrap, "_create_venv", create)
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: None)
    bootstrap.ensure_venv(project)
    assert entered == [venv / "bin" / "python"]
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._COMPLETE_MARKER_CONTENT.encode()
    assert (venv / bootstrap._SETUP_WITNESS).read_bytes() == bootstrap._WITNESS_CONTENT.encode()


def test_witness_sync_failure_leaves_evidence_and_blocks_retry(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_bytes(b"")
    witness = project / ".venv" / bootstrap._SETUP_WITNESS
    marker = project / ".venv" / bootstrap._INCOMPLETE_MARKER
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("created after failed witness sync"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered after failed sync"))
    real_fsync = os.fsync

    def fail_witness(fd):
        from core.file_identity import same_file_identity
        if witness.exists() and same_file_identity(os.fstat(fd), witness.stat()):
            raise OSError("witness sync failed")
        return real_fsync(fd)

    monkeypatch.setattr(bootstrap.os, "fsync", fail_witness)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert witness.read_bytes() == bootstrap._WITNESS_CONTENT.encode()
    assert not marker.exists()
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert witness.read_bytes() == bootstrap._WITNESS_CONTENT.encode()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not rename the open witness")
def test_witness_replacement_stops_before_dependency_install(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_bytes(b"")
    venv = project / ".venv"
    witness = venv / bootstrap._SETUP_WITNESS
    outside = project / "user-file"
    outside.write_bytes(b"preserve")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("installed after witness replacement"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered after witness replacement"))

    def create(*args):
        witness.rename(project / "original-witness")
        witness.symlink_to(outside)
        (venv / "bin").mkdir()
        (venv / "bin" / "python").touch()

    monkeypatch.setattr(bootstrap, "_create_venv", create)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert outside.read_bytes() == b"preserve"
    assert witness.is_symlink()
    assert (project / "original-witness").read_bytes() == bootstrap._WITNESS_CONTENT.encode()
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory fsync")
@pytest.mark.parametrize("failed_dir", ["parent", "venv"])
def test_directory_sync_failure_preserves_evidence_and_stops(tmp_path, monkeypatch, capsys, failed_dir):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_bytes(b"")
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "system-python")
    monkeypatch.setattr(bootstrap, "_create_venv", lambda *args: pytest.fail("created after failed directory sync"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered after failed directory sync"))
    real_fsync = os.fsync

    def fail_directory(fd):
        from core.file_identity import same_file_identity
        target = project if failed_dir == "parent" else venv
        if target.exists() and same_file_identity(os.fstat(fd), target.stat()):
            raise OSError("directory sync unsupported")
        return real_fsync(fd)

    monkeypatch.setattr(bootstrap.os, "fsync", fail_directory)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert "sync" in capsys.readouterr().out.lower()
    if failed_dir == "parent":
        assert not venv.exists()
    else:
        assert (venv / bootstrap._SETUP_WITNESS).read_bytes() == bootstrap._WITNESS_CONTENT.encode()
        assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()
        with pytest.raises(SystemExit):
            bootstrap.ensure_venv(project)


def test_new_directory_identity_change_is_detected_before_completion(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
def test_new_environment_does_not_inherit_world_writable_umask(tmp_path):
    """An isolated permissive umask must not expose setup to other accounts."""
    project = Path(__file__).resolve().parents[1]
    code = r'''
import os, stat, sys
from pathlib import Path
from core import venv_bootstrap as bootstrap

root = Path(sys.argv[1])
root.mkdir()
(root / "requirements.txt").write_text("", encoding="utf-8")
os.umask(0)
sys.prefix = sys.base_prefix = str(root / "system")
os.environ.pop("VIRTUAL_ENV", None)
bootstrap._find_python = lambda version: "system-python"
def stop(*args):
    raise SystemExit(23)
bootstrap._create_venv = stop
if sys.argv[2] == "legacy":
    mkdir = Path.mkdir
    def legacy_mkdir(path, mode=0o777, *args, **kwargs):
        return mkdir(path, 0o777, *args, **kwargs)
    Path.mkdir = legacy_mkdir
try:
    bootstrap.ensure_venv(root)
except SystemExit as exc:
    assert exc.code == 23
else:
    raise AssertionError("setup did not reach creation")
print(oct(stat.S_IMODE((root / ".venv").stat().st_mode)))
'''
    modes = []
    for variant in ("legacy", "current"):
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path / variant), variant],
                                cwd=project, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        modes.append(int(result.stdout.strip().splitlines()[-1], 8))
    if modes[0] & 0o022 != 0o022:
        pytest.skip("filesystem does not expose permissive POSIX mode bits")
    assert modes[1] & 0o022 == 0


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
def test_new_environment_rejects_already_injected_hardlink(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
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
@pytest.mark.parametrize("attack", ["directory", "hardlink"])
def test_venv_package_preparation_race_preserves_user_data(tmp_path, monkeypatch, attack):
    """Recheck after potentially slow system package setup and before venv runs."""
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    venv = project / ".venv"
    victim = project / ("user-directory" if attack == "directory" else "user-file")
    if attack == "directory":
        victim.mkdir()
        user_file = victim / "pyvenv.cfg"
    else:
        user_file = victim
    original = b"user-owned configuration\n"
    user_file.write_bytes(original)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: real_python)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("unexpected reentry"))
    run = subprocess.run

    def inject_during_package_setup(_python):
        if attack == "directory":
            code = "import os,sys; os.rename(sys.argv[1],sys.argv[2]); os.rename(sys.argv[3],sys.argv[1])"
            args = [str(venv), str(project / "original-venv"), str(victim)]
        else:
            code = "import os,sys; os.link(sys.argv[1],sys.argv[2])"
            args = [str(victim), str(venv / "pyvenv.cfg")]
        run([real_python, "-c", code, *args], check=True, timeout=15)

    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", inject_during_package_setup)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs: pytest.fail("venv command ran"))
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    final_file = venv / "pyvenv.cfg" if attack == "directory" else victim
    assert final_file.read_bytes() == original


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permits renaming the open setup directory")
def test_venv_create_rejects_replaced_directory_with_forged_marker(tmp_path, monkeypatch):
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    venv = project / ".venv"
    victim = project / "user-directory"
    victim.mkdir()
    forged = victim / bootstrap._INCOMPLETE_MARKER
    forged.write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: real_python)
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda *_: pytest.fail("package preparation ran"))
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *_: pytest.fail("unexpected reentry"))
    create = bootstrap._create_venv

    def swap_before_create(python, directory, opened, marker, *held):
        subprocess.run([real_python, "-c",
                        "import os,sys; os.rename(sys.argv[1],sys.argv[2]); os.rename(sys.argv[3],sys.argv[1])",
                        str(venv), str(project / "original-venv"), str(victim)],
                       check=True, timeout=15)
        return create(python, directory, opened, marker, *held)

    monkeypatch.setattr(bootstrap, "_create_venv", swap_before_create)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert (project / "original-venv" / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()
    assert not (venv / "pyvenv.cfg").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
@pytest.mark.parametrize("attack", ["directory", "hardlink", "symlink"])
def test_failed_venv_command_does_not_retry_over_substituted_data(tmp_path, monkeypatch, attack):
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    venv = project / ".venv"
    victim = project / ("user-directory" if attack == "directory" else "user-file")
    if attack == "directory":
        victim.mkdir()
        user_file = victim / "pyvenv.cfg"
    else:
        user_file = victim
    original = b"user-owned configuration\n"
    user_file.write_bytes(original)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: real_python)
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda *_: None)
    run = subprocess.run
    calls = []

    def first_attempt(command, **kwargs):
        calls.append(command)
        if len(calls) != 1:
            pytest.fail("unsafe venv retry ran")
        if attack == "directory":
            code = "import os,sys; os.rename(sys.argv[1],sys.argv[2]); os.rename(sys.argv[3],sys.argv[1])"
            args = [str(venv), str(project / "original-venv"), str(victim)]
        else:
            operation = "symlink" if attack == "symlink" else "link"
            code = f"import os,sys; os.{operation}(sys.argv[1],sys.argv[2])"
            args = [str(victim), str(venv / "pyvenv.cfg")]
        run([real_python, "-c", code, *args], check=True, timeout=15)
        return SimpleNamespace(returncode=1, stdout="", stderr="ensurepip unavailable")

    monkeypatch.setattr(bootstrap.subprocess, "run", first_attempt)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert len(calls) == 1
    final_file = venv / "pyvenv.cfg" if attack == "directory" else victim
    assert final_file.read_bytes() == original


def test_new_environment_bootstraps_pip_after_venv_creation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PYTHONHOME", "outside-home")
    monkeypatch.setenv("PYTHONPATH", "outside-modules")
    monkeypatch.setattr(bootstrap, "_find_python", lambda _: "system-python")
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda *_: None)
    calls = []

    def simulate(command, **kwargs):
        calls.append(command)
        assert "PYTHONHOME" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        if command[1:4] == ["-I", "-m", "venv"]:
            assert "--without-pip" in command
            (venv / "bin").mkdir()
            (venv / "bin" / "python").touch()
            (venv / "pyvenv.cfg").write_text("normal partial setup", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1:5] == ["-I", "-m", "pip", "--version"]:
            previous_pip_checks = sum(c[1:5] == ["-I", "-m", "pip", "--version"] for c in calls)
            return SimpleNamespace(returncode=1 if previous_pip_checks == 1 else 0)
        if command[1:4] == ["-I", "-m", "ensurepip"]:
            assert command[4:] == ["--upgrade", "--default-pip"]
            return SimpleNamespace(returncode=0)
        assert command[1:5] == ["-I", "-m", "pip", "install"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", simulate)
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)
    bootstrap.ensure_venv(project)
    assert [command[3] for command in calls] == ["venv", "pip", "ensurepip", "pip", "pip"]
    assert entered == [venv / "bin" / "python"]
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._COMPLETE_MARKER_CONTENT.encode()


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
def test_failed_venv_command_preserves_substituted_regular_config(tmp_path, monkeypatch):
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
    victim = project / "user-config"
    original = b"user-owned configuration\n"
    victim.write_bytes(original)
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda _: real_python)
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda *_: None)
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *_: None)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *_: None)
    run = subprocess.run
    calls = []

    def replace_before_retry(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            run([real_python, "-c", "import os,sys; os.rename(sys.argv[1],sys.argv[2])",
                 str(victim), str(venv / "pyvenv.cfg")], check=True, timeout=15)
            return SimpleNamespace(returncode=1, stdout="", stderr="ensurepip unavailable")
        return run(command, **kwargs)

    monkeypatch.setattr(bootstrap.subprocess, "run", replace_before_retry)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert len(calls) == 1
    assert (venv / "pyvenv.cfg").read_bytes() == original


@pytest.mark.skipif(sys.platform == "win32", reason="automatic creation is only used on POSIX")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A17: venv follows the target path after the identity check")
def test_new_environment_replacement_cannot_overwrite_user_data(tmp_path, monkeypatch):
    """A real venv must not write into a replacement directory, even temporarily."""
    real_python = sys.executable
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("", encoding="utf-8")
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

    def replace_before_create(python_bin, venv_dir, *identity):
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
    (project / "requirements.txt").write_text("", encoding="utf-8")
    victim = project / "user-config"
    original = b"user-owned configuration\n"
    victim.write_bytes(original)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: real_python)
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: None)

    def create_with_link(python_bin, venv_dir, *identity):
        os.link(victim, venv_dir / "pyvenv.cfg")
        subprocess.run([python_bin, "-m", "venv", "--without-pip", str(venv_dir)],
                       check=True, capture_output=True, timeout=30)

    monkeypatch.setattr(bootstrap, "_create_venv", create_with_link)
    bootstrap.ensure_venv(project)
    assert victim.read_bytes() == original


def test_successful_venv_command_without_interpreter_does_not_install(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / bootstrap._INCOMPLETE_MARKER).write_bytes(bootstrap._MARKER_CONTENT.encode())
    monkeypatch.setattr(bootstrap, "_ensure_venv_pkg", lambda path: None)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(bootstrap, "_install_dependencies", lambda *args: pytest.fail("installed without interpreter"))
    with pytest.raises(SystemExit):
        bootstrap._create_venv("python", venv)


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


@pytest.mark.parametrize(("lang", "expected"), [("en_US.UTF-8", "Dependency file"),
                                               ("zh_CN.UTF-8", "依赖文件")])
@pytest.mark.parametrize("manifest_type", ["missing", "directory"])
def test_missing_requirements_stops_before_creating_environment(tmp_path, monkeypatch, capsys, lang, expected, manifest_type):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("LC_ALL", lang)
    if manifest_type == "directory":
        (project / "requirements.txt").mkdir()
    monkeypatch.setattr(bootstrap, "_find_python", lambda *args: pytest.fail("searched before manifest check"))
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    message = capsys.readouterr().out
    assert expected in message and "requirements.txt" in message
    assert not (project / ".venv").exists()


def test_missing_requirements_during_creation_cannot_complete_setup(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text("example-package\n", encoding="utf-8")
    venv = project / ".venv"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap, "_find_python", lambda version: "python")
    monkeypatch.setattr(bootstrap, "_exec_reenter", lambda *args: pytest.fail("reentered incomplete environment"))

    def create_environment(*args):
        (venv / "bin").mkdir()
        (venv / "bin" / "python").touch()
        (venv / "pyvenv.cfg").touch()
        requirements.unlink()

    monkeypatch.setattr(bootstrap, "_create_venv", create_environment)
    with pytest.raises(SystemExit):
        bootstrap.ensure_venv(project)
    assert (venv / bootstrap._INCOMPLETE_MARKER).read_bytes() == bootstrap._MARKER_CONTENT.encode()


def test_prepared_environment_is_reused_without_requirements_manifest(tmp_path, monkeypatch):
    project = tmp_path / "project"
    executable = project / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "base" / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    entered = []
    monkeypatch.setattr(bootstrap, "_exec_reenter", entered.append)
    bootstrap.ensure_venv(project)
    assert entered == [executable]
