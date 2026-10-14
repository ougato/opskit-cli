"""Registry write failures must never be reported as successful PATH changes."""
from types import SimpleNamespace
import os
import sys
import uuid

import pytest

from software._shared.windows_registry_path import path_transaction
from software.base import InstallError


@pytest.fixture
def registry(monkeypatch):
    values = {"PATH": ("C:\\Windows;;D:\\Tools", 2)}
    closed = []
    def read(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name]
    api = SimpleNamespace(HKEY_CURRENT_USER="user", KEY_READ=1, KEY_SET_VALUE=2, REG_SZ=1, REG_EXPAND_SZ=2,
                          OpenKey=lambda *args: "key", QueryValueEx=read,
                          SetValueEx=lambda key,name,reserved,kind,value: values.update({name: (value,kind)}),
                          DeleteValue=lambda key,name: values.pop(name), CloseKey=lambda key: closed.append(key))
    monkeypatch.setitem(sys.modules, "winreg", api)
    return api, values, closed


@pytest.mark.parametrize("kind", [1, 2])
def test_publish_preserves_type_and_unrelated_empty_entries(registry, kind):
    _, values, closed = registry
    values["PATH"] = ("C:\\Windows;;D:\\Tools", kind)
    with path_transaction("C:\\Managed"):
        assert values["PATH"] == ("C:\\Managed;C:\\Windows;;D:\\Tools", kind)
    assert closed == ["key"]


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_callback_failure_restores_exact_presence_value_and_type(registry, missing, cancel):
    _, values, closed = registry
    if missing:
        values.clear()
    before = dict(values)
    with pytest.raises(KeyboardInterrupt if cancel else PermissionError):
        with path_transaction("C:\\Managed"):
            raise KeyboardInterrupt() if cancel else PermissionError("profile failure")
    assert values == before and closed == ["key"]


def test_partial_registry_write_is_compensated(registry):
    api, values, _ = registry
    before = dict(values)
    def partial(key, name, reserved, kind, value):
        values[name] = (value, kind)
        if value.startswith("C:\\Managed"):
            raise PermissionError("partial write failure")
    api.SetValueEx = partial
    with pytest.raises(PermissionError):
        with path_transaction("C:\\Managed"):
            pytest.fail("failed publication entered callback")
    assert values == before


def test_external_registry_change_is_preserved(registry):
    _, values, closed = registry
    with pytest.raises(InstallError, match="rollback incomplete"):
        with path_transaction("C:\\Managed"):
            values["PATH"] = ("administrator replacement", 1)
            raise PermissionError("profile failure")
    assert values["PATH"] == ("administrator replacement", 1) and closed == ["key"]


def test_external_change_cannot_be_reported_as_success(registry):
    _, values, closed = registry
    with pytest.raises(InstallError, match="rollback incomplete"):
        with path_transaction("C:\\Managed"):
            values["PATH"] = ("administrator replacement", 1)
    assert values["PATH"] == ("administrator replacement", 1) and closed == ["key"]


@pytest.mark.parametrize("external_profile", [False, True])
def test_joint_transaction_compensates_other_resource(registry, tmp_path, monkeypatch, external_profile):
    from pathlib import Path
    from software._shared import windows_profile
    from software._shared.windows_registry_path import change_user_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profiles = windows_profile._profiles()
    for path in profiles:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# original\r\n")
    before = [path.read_bytes() for path in profiles]
    api, values, _ = registry
    previous_registry = dict(values)
    if external_profile:
        original = api.SetValueEx
        def interfere(*args):
            original(*args)
            if args[-1].startswith("C:\\Managed"):
                profiles[0].write_bytes(b"# administrator replacement\r\n")
        api.SetValueEx = interfere
    else:
        original = api.QueryValueEx
        reads = []
        def interfere(*args):
            reads.append(True)
            if len(reads) == 3:
                values["PATH"] = ("administrator replacement", 1)
            return original(*args)
        api.QueryValueEx = interfere
    with pytest.raises(InstallError, match="rollback incomplete"):
        change_user_path("C:\\Managed", "# begin", "# end")
    if external_profile:
        assert values == previous_registry
        assert profiles[0].read_bytes() == b"# administrator replacement\r\n"
        assert [path.read_bytes() for path in profiles[1:]] == before[1:]
    else:
        assert values["PATH"] == ("administrator replacement", 1)
        assert [path.read_bytes() for path in profiles] == before


@pytest.mark.parametrize("external_command", [False, True])
def test_command_verification_failure_compensates_registry_and_profiles(registry, tmp_path, monkeypatch, external_command):
    from pathlib import Path
    from software._shared import windows_commands, windows_profile
    from software._shared.windows_registry_path import change_user_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profiles = windows_profile._profiles()
    for path in profiles:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# previous\r\n")
    before = [path.read_bytes() for path in profiles]
    api, values, _ = registry
    prior_registry = dict(values)
    directory = tmp_path / "commands"
    replacements = {"first.cmd": "known first", "second.cmd": "known second"}
    if external_command:
        original = api.SetValueEx
        def interfere(*args):
            original(*args)
            if args[-1].startswith("C:\\Managed"):
                (directory / "first.cmd").write_bytes(b"external replacement")
        api.SetValueEx = interfere
    else:
        original = api.QueryValueEx
        calls = []
        def interfere(*args):
            calls.append(True)
            if len(calls) == 3:
                values["PATH"] = ("external registry replacement", 1)
            return original(*args)
        api.QueryValueEx = interfere
    with pytest.raises(InstallError, match="rollback incomplete"):
        with windows_commands.command_transaction(directory, replacements, owned=lambda name, body: body == replacements[name]) as verify:
            change_user_path("C:\\Managed", "# begin", "# end", verify_commands=verify)
    assert [path.read_bytes() for path in profiles] == before
    assert not (directory / "second.cmd").exists()
    if external_command:
        assert values == prior_registry
        assert (directory / "first.cmd").read_bytes() == b"external replacement"
    else:
        assert values["PATH"] == ("external registry replacement", 1)
        assert not directory.exists()


def test_removal_matches_windows_case_and_separator_policy(registry):
    _, values, _ = registry
    values["PATH"] = ('C:\\Windows;"c:/managed";D:\\Tools', 1)
    with path_transaction("C:\\Managed", remove=True):
        pass
    assert values["PATH"] == ("C:\\Windows;D:\\Tools", 1)


@pytest.mark.parametrize("kind", [1, 2])
def test_batch_removal_publishes_once_and_preserves_unrelated_entries(registry, kind):
    api, values, _ = registry
    values["PATH"] = ('C:\\Windows;;"c:/managed/active";D:\\Tools;C:\\Managed\\bin;C:\\MANAGED\\ACTIVE', kind)
    writes = []
    original = api.SetValueEx
    def write(*args):
        writes.append(args)
        original(*args)
    api.SetValueEx = write
    with path_transaction("C:\\Managed\\active", remove=True, other_directories=("C:\\Managed\\bin",)):
        assert values["PATH"] == ("C:\\Windows;;D:\\Tools", kind)
    assert len(writes) == 1


@pytest.mark.parametrize("failure", ["callback", "cancel", "partial_write", "external"])
def test_batch_removal_failure_preserves_previous_or_external_value(registry, failure):
    api, values, _ = registry
    before = ("C:\\Managed\\active;C:\\Windows;C:\\Managed\\bin", 2)
    values["PATH"] = before
    if failure == "partial_write":
        original = api.SetValueEx
        def write(*args):
            original(*args)
            if args[-1] == "C:\\Windows":
                raise PermissionError("partial publication")
        api.SetValueEx = write
    error = KeyboardInterrupt if failure == "cancel" else InstallError if failure == "external" else PermissionError
    with pytest.raises(error):
        with path_transaction("C:\\Managed\\active", remove=True, other_directories=("C:\\Managed\\bin",)):
            if failure == "external":
                values["PATH"] = ("administrator replacement", 1)
            elif failure == "cancel":
                raise KeyboardInterrupt()
            else:
                raise PermissionError("late failure")
    assert values["PATH"] == (("administrator replacement", 1) if failure == "external" else before)


@pytest.mark.parametrize("entry", ["relative", "C:\\bad;entry", "C:\\bad\nentry"])
def test_batch_removal_validates_every_directory_before_opening_key(registry, entry):
    api, values, closed = registry
    before = dict(values)
    api.OpenKey = lambda *args: pytest.fail("invalid group opened registry")
    with pytest.raises(ValueError, match="invalid registry PATH directory"):
        with path_transaction("C:\\Managed", remove=True, other_directories=(entry,)):
            pytest.fail("invalid group entered callback")
    assert values == before and not closed


def test_path_growth_limit_rejects_publication(registry):
    api, values, _ = registry
    values["PATH"] = ("x" * 32760, 2)
    before = dict(values)
    api.SetValueEx = lambda *args: pytest.fail("oversized PATH published")
    with pytest.raises(ValueError, match="limit after update"):
        with path_transaction("C:\\Managed"):
            pytest.fail("oversized PATH entered callback")
    assert values == before


@pytest.mark.parametrize("recipe", ["python", "nodejs", "golang", "java"])
@pytest.mark.parametrize("remove", [False, True])
def test_driver_profile_failure_restores_user_registry(tmp_path, monkeypatch, recipe, remove):
    import ctypes
    import importlib
    from pathlib import Path
    from software._shared import windows_profile
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    directory = str(tmp_path / "shims")
    initial = (directory + ";C:\\Windows" if remove else "C:\\Windows", 2)
    values = {"user": initial, "machine": ("C:\\Windows", 2)}
    def query(key, name):
        if name != "PATH":
            raise FileNotFoundError(name)
        return values[key]
    api = SimpleNamespace(HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="machine", KEY_READ=1, KEY_SET_VALUE=2,
                          REG_SZ=1, REG_EXPAND_SZ=2, OpenKey=lambda hive,*args: hive, QueryValueEx=query,
                          SetValueEx=lambda key,name,reserved,kind,value: values.update({key: (value,kind)}), CloseKey=lambda key: None)
    monkeypatch.setitem(sys.modules, "winreg", api)
    notification = SimpleNamespace(SendMessageTimeoutW=lambda *args: 1)
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll, "user32", notification)
    else:
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=notification), raising=False)
    def fail(*args, **kwargs):
        raise PermissionError("profile failed")
    monkeypatch.setattr(windows_profile, "profile_transaction", fail)
    driver = importlib.import_module(f"software.recipes.{recipe}.windows").WindowsDriver()
    with pytest.raises(PermissionError, match="profile failed"):
        getattr(driver, "_remove_path" if remove else "_inject_path")(directory)
    assert values["user"] == initial
    assert values["machine"] == ("C:\\Windows", 2)


@pytest.mark.parametrize("recipe", ["python", "nodejs", "golang", "java"])
@pytest.mark.parametrize("remove", [False, True])
def test_user_driver_never_accesses_machine_environment(registry, tmp_path, monkeypatch, recipe, remove):
    import importlib
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    api, _, _ = registry
    # Intentionally no HKEY_LOCAL_MACHINE attribute: user-scoped software must
    # not need machine registry access even when run by an administrator.
    def open_user(hive, *args):
        assert hive == "user"
        return "key"
    api.OpenKey = open_user
    driver = importlib.import_module(f"software.recipes.{recipe}.windows").WindowsDriver()
    getattr(driver, "_remove_path" if remove else "_inject_path")(str(tmp_path / "shims"))


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows registry")
@pytest.mark.parametrize("kind", [1, 2])
def test_native_temporary_key_retains_type_and_rolls_back(kind):
    import winreg
    subkey = "Software\\OpsKitPathTest-" + uuid.uuid4().hex
    original = ("C:\\Windows;;D:\\Tools", kind)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PATH", 0, kind, original[0])
        with pytest.raises(PermissionError, match="injected callback"):
            with path_transaction("C:\\Managed", subkey=subkey):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                    assert winreg.QueryValueEx(key, "PATH") == ("C:\\Managed;" + original[0], kind)
                raise PermissionError("injected callback failure")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "PATH") == original
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows registry")
@pytest.mark.parametrize("kind", [1, 2])
def test_native_batch_removal_commits_and_compensates_in_temporary_key(kind):
    import winreg
    subkey = "Software\\OpsKitPathTest-" + uuid.uuid4().hex
    original = ('C:\\Managed\\active;;"c:/managed/bin";C:\\Windows;C:\\MANAGED\\ACTIVE', kind)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PATH", 0, kind, original[0])
        with pytest.raises(KeyboardInterrupt):
            with path_transaction("C:\\Managed\\active", remove=True,
                                  other_directories=("C:\\Managed\\bin",), subkey=subkey):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                    assert winreg.QueryValueEx(key, "PATH") == (";C:\\Windows", kind)
                raise KeyboardInterrupt()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "PATH") == original
        with path_transaction("C:\\Managed\\active", remove=True,
                              other_directories=("C:\\Managed\\bin",), subkey=subkey):
            pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "PATH") == (";C:\\Windows", kind)
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows junctions")
@pytest.mark.parametrize("feature", ["nodejs", "golang", "java"])
@pytest.mark.parametrize("mode", ["create", "switch", "restore"])
@pytest.mark.parametrize("cancel", [False, True])
def test_native_driver_registry_failure_restores_route_commands_profiles(
        registry, tmp_path, monkeypatch, feature, mode, cancel):
    import importlib
    from pathlib import Path
    from software._shared import windows_junction, windows_profile
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    driver = importlib.import_module(f"software.recipes.{feature}.windows").WindowsDriver()
    # AutoRun is a separately tested migration; this test must never open the
    # real user's registry through that independent resource.
    monkeypatch.setattr(driver, "_retire_legacy_autorun", lambda: None)
    entry = driver._active_junction()
    root = entry.parent
    first, second = root / "first", root / "second"
    for target in (first, second):
        target.mkdir(parents=True)
        (target / "bin").mkdir()
    def binary(target):
        return str(target if feature == "nodejs" else target / "bin")
    if mode != "create":
        driver.apply_version_link(binary(first))
    if mode == "restore":
        driver.install_shim("")
    directory = driver._win_bin_dir()
    files_before = {p.name: p.read_bytes() for p in directory.glob("*.cmd")}
    profiles = windows_profile._profiles()
    profiles_before = [p.read_bytes() if p.exists() else None for p in profiles]
    state_before = windows_junction._state(entry, root)
    api, values, _ = registry
    if mode == "switch":
        # Force a PATH publication even when the active path is already first.
        values["PATH"] = ("C:\\Windows", 2)
    if mode == "restore":
        active_bin = entry if feature == "nodejs" else entry / "bin"
        values["PATH"] = (str(active_bin) + ";C:\\Windows;" + str(directory), 2)
    registry_before = dict(values)
    original = api.SetValueEx
    failed = []
    def fail_once(*args):
        original(*args)
        if not failed:
            failed.append(True)
            raise KeyboardInterrupt() if cancel else PermissionError("late registry publication failure")
    api.SetValueEx = fail_once
    expected = KeyboardInterrupt if cancel else InstallError if mode == "restore" else PermissionError
    with pytest.raises(expected) as caught:
        if mode == "restore":
            driver.restore_original()
        else:
            driver.apply_version_link(binary(second))
    if mode == "restore" and not cancel:
        assert str(caught.value) == "command file operation failed"
        assert isinstance(caught.value.__cause__, PermissionError)
    assert failed and values == registry_before
    assert [p.read_bytes() if p.exists() else None for p in profiles] == profiles_before
    assert {p.name: p.read_bytes() for p in directory.glob("*.cmd")} == files_before
    state_after = windows_junction._state(entry, root)
    if state_before is None:
        assert state_after is None
    else:
        assert (state_after.data, state_after.security, state_after.protected) == (
            state_before.data, state_before.security, state_before.protected)
        assert entry.resolve() == first
