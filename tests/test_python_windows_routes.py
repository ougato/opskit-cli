"""Native Python routes preserve external environments and compensate failures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from software.recipes.python import common
from software.recipes.python.windows import WindowsDriver
from software._shared import windows_commands, windows_junction

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions and cmd")


@pytest.fixture
def driver(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(common, "load_snapshot", lambda: {})
    item = WindowsDriver()
    monkeypatch.setattr(item, "_inject_path", lambda *a, **kw: kw["verify_commands"]())
    monkeypatch.setattr(item, "_remove_path", lambda *a, **kw: kw["verify_commands"]())
    return item


def execute(command, probe, arguments):
    line = '"' + str(command) + '" "' + str(probe) + '" ' + subprocess.list2cmdline(arguments)
    return subprocess.run('cmd.exe /d /v:off /s /c "' + line + '"', capture_output=True,
                          text=True, encoding="utf-8", timeout=15)


@pytest.mark.parametrize("binary", [Path(sys.base_prefix) / "python.exe", Path(sys.executable)])
def test_external_interpreters_receive_exact_arguments_and_exit_code(driver, tmp_path, binary):
    driver.apply_version_link(str(binary))
    driver.install_shim("")
    probe = tmp_path / "probe.py"
    probe.write_text('import json,sys; print(json.dumps({"args":sys.argv[1:],"prefix":sys.prefix})); sys.exit(37)')
    arguments = ["中文 空格", "literal & value", "percent%", "bang!", 'quote"inside', "trailing\\"]
    expected_root = driver._route_plan(str(binary))[0]
    for name in ("python.cmd", "python3.cmd"):
        result = execute(common.shim_dir() / name, probe, arguments)
        assert result.returncode == 37, result.stderr
        payload = json.loads(result.stdout)
        assert payload["args"] == arguments
        assert Path(payload["prefix"]).resolve() == expected_root
        assert "findstr" not in (common.shim_dir() / name).read_text()
    driver.restore_original("", None, False)
    driver.remove_shim()
    assert not os.path.lexists(common.shim_dir() / ".python-current")
    assert not list(common.shim_dir().glob("*.cmd"))
    assert binary.is_file()


@pytest.mark.parametrize("cancel", [False, True])
def test_remove_path_failure_restores_commands_and_external_junction(driver, monkeypatch, cancel):
    binary = Path(sys.base_prefix) / "python.exe"
    driver.apply_version_link(str(binary))
    before = {p.name: p.read_bytes() for p in common.shim_dir().glob("*.cmd")}
    entry = common.shim_dir() / ".python-current"
    def fail(*args, **kwargs):
        raise KeyboardInterrupt() if cancel else RuntimeError("profile publication failed")
    monkeypatch.setattr(driver, "_remove_path", fail)
    with pytest.raises(KeyboardInterrupt if cancel else RuntimeError):
        driver.remove_shim()
    assert entry.resolve() == binary.parent.resolve()
    assert {p.name: p.read_bytes() for p in common.shim_dir().glob("*.cmd")} == before
    monkeypatch.setattr(driver, "_remove_path", lambda *a, **kw: kw["verify_commands"]())
    driver.remove_shim()


def test_switch_second_command_failure_preserves_old_route(driver, monkeypatch):
    first = Path(sys.base_prefix) / "python.exe"
    second = Path(sys.executable)
    driver.apply_version_link(str(first))
    before = {p.name: p.read_bytes() for p in common.shim_dir().glob("*.cmd")}
    publish = windows_commands._publish_command
    def fail(path, text):
        if path.name == "python3.cmd" and "Scripts" in text:
            raise OSError("publication failed")
        return publish(path, text)
    monkeypatch.setattr(windows_commands, "_publish_command", fail)
    with pytest.raises(Exception, match="failed"):
        driver.apply_version_link(str(second))
    assert (common.shim_dir() / ".python-current").resolve() == first.parent.resolve()
    assert {p.name: p.read_bytes() for p in common.shim_dir().glob("*.cmd")} == before
    driver.remove_shim()


def test_unknown_junction_is_not_claimed_from_its_own_target(driver, tmp_path):
    directory = common.shim_dir()
    directory.mkdir(parents=True)
    unknown = tmp_path / "foreign"
    unknown.mkdir()
    entry = directory / ".python-current"
    windows_junction.set_junction(entry, unknown, directory, authorized_targets=(unknown,))
    try:
        with pytest.raises(Exception, match="explicitly authorized"):
            driver.apply_version_link(str(Path(sys.base_prefix) / "python.exe"))
        assert entry.resolve() == unknown
        assert not list(directory.glob("*.cmd"))
    finally:
        windows_junction.remove_junction(entry, directory, authorized_targets=(unknown,))


@pytest.mark.parametrize("existing", [False, True])
def test_install_activation_path_failure_restores_snapshot_and_route(driver, monkeypatch, existing):
    from software.recipes.python import recipe
    first = Path(sys.base_prefix) / "python.exe"
    second = Path(sys.executable)
    previous = {"uv_python_path": str(first), "active_version": "previous", "installed_versions": ["previous"]} if existing else {}
    state = dict(previous)
    if existing:
        driver.apply_version_link(str(first))
    monkeypatch.setattr(common, "load_snapshot", lambda: dict(state))
    def save(value):
        state.clear()
        state.update(value)
    monkeypatch.setattr(recipe, "save_snapshot", save)
    def reject(*args, **kwargs):
        raise RuntimeError("PATH publication failed")
    monkeypatch.setattr(driver, "_inject_path", reject)
    instance = recipe.PythonRecipe()
    version = ".".join(map(str, sys.version_info[:3]))
    with pytest.raises(RuntimeError, match="PATH publication failed"):
        instance._activate_install(version, str(second), previous, driver)
    assert state == previous
    entry = common.shim_dir() / ".python-current"
    if existing:
        assert entry.resolve() == first.parent.resolve()
        driver.remove_shim()
    else:
        assert not os.path.lexists(entry)
        assert not list(common.shim_dir().glob("*.cmd"))


@pytest.mark.parametrize("newline", ["\r\n", "\n", "\r\r\n"])
def test_full_legacy_template_migrates_to_static_route(driver, newline):
    from software.recipes.python.constants import SHIM_CMD_TEMPLATE
    directory = common.shim_dir()
    directory.mkdir(parents=True)
    for name in ("python.cmd", "python3.cmd"):
        (directory / name).write_bytes(SHIM_CMD_TEMPLATE.replace("\r\n", newline).encode("ascii"))
    driver.apply_version_link(str(Path(sys.base_prefix) / "python.exe"))
    assert all("findstr" not in path.read_text() for path in directory.glob("*.cmd"))
    driver.remove_shim()


def test_recipe_switch_after_snapshot_publication_uses_bound_old_target(driver, monkeypatch):
    from software.recipes.python import recipe
    first = Path(sys.base_prefix) / "python.exe"
    second = Path(sys.executable)
    driver.apply_version_link(str(first))
    state = {"uv_python_path": str(first), "active_version": "previous"}
    monkeypatch.setattr(common, "load_snapshot", lambda: dict(state))
    monkeypatch.setattr(recipe, "load_snapshot", lambda: dict(state))
    def save(value):
        state.clear()
        state.update(value)
    monkeypatch.setattr(recipe, "save_snapshot", save)
    monkeypatch.setattr(recipe, "find_uv_python", lambda version: str(second))
    # A fresh driver must bind the old snapshot before activate_version saves
    # its new one. Reusing the earlier driver would hide that ordering bug.
    fresh = WindowsDriver()
    monkeypatch.setattr(recipe, "get_driver", lambda: fresh)
    monkeypatch.setenv("PATH", str(common.shim_dir()) + ";" + os.environ.get("PATH", ""))
    version = ".".join(map(str, sys.version_info[:3]))
    recipe.PythonRecipe().switch(version)
    assert state["uv_python_path"] == str(second)
    assert state["active_version"] == version
    assert (common.shim_dir() / ".python-current").resolve() == fresh._route_plan(str(second))[0]
    monkeypatch.setattr(fresh, "_remove_path", lambda *a, **kw: kw["verify_commands"]())
    fresh.remove_shim()


@pytest.mark.parametrize("kind", [1, 2])
@pytest.mark.parametrize("cancel", [False, True])
def test_native_temporary_registry_partial_removal_restores_all_resources(driver, monkeypatch, kind, cancel):
    import uuid
    import winreg
    from software._shared import windows_profile, windows_registry_path
    fresh = WindowsDriver()
    monkeypatch.setattr(fresh, "_retire_legacy_autorun", lambda: None)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    subkey = "Software\\OpsKitPythonRouteTest-" + uuid.uuid4().hex
    original_transaction = windows_registry_path.path_transaction
    monkeypatch.setattr(windows_registry_path, "path_transaction",
                        lambda *a, **kw: original_transaction(*a, subkey=subkey, **kw))
    original_value = ("C:\\Windows;;D:\\Tools", kind)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PATH", 0, kind, original_value[0])
        fresh.apply_version_link(str(Path(sys.base_prefix) / "python.exe"))
        fresh.install_shim("")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            installed_value = winreg.QueryValueEx(key, "PATH")
        profiles = {path: path.read_bytes() for path in windows_profile._profiles()}
        commands = {path: path.read_bytes() for path in common.shim_dir().glob("*.cmd")}
        process_path = os.environ["PATH"]
        original_write = winreg.SetValueEx
        injected = []
        def partial_write(*args):
            original_write(*args)
            if args[-1] == original_value[0] and not injected:
                injected.append(True)
                raise KeyboardInterrupt() if cancel else PermissionError("partial registry write")
        monkeypatch.setattr(winreg, "SetValueEx", partial_write)
        from software.base import InstallError
        with pytest.raises(KeyboardInterrupt if cancel else InstallError) as failure:
            fresh.remove_shim()
        if not cancel:
            assert isinstance(failure.value.__cause__, PermissionError)
        assert injected
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "PATH") == installed_value
        assert {path: path.read_bytes() for path in profiles} == profiles
        assert {path: path.read_bytes() for path in commands} == commands
        assert (common.shim_dir() / ".python-current").resolve() == Path(sys.base_prefix).resolve()
        assert os.environ["PATH"] == process_path
        monkeypatch.setattr(winreg, "SetValueEx", original_write)
        fresh.remove_shim()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "PATH") == original_value
        assert all(b"opskit-shims-path-begin" not in path.read_bytes() for path in profiles)
        assert not os.path.lexists(common.shim_dir() / ".python-current")
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
