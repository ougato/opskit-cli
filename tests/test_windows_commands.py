"""Command ownership, joint compensation and native batch routing."""
import importlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from software._shared import windows_commands as commands
from software._shared import shell_path
from software.base import InstallError


def _driver(tmp_path, monkeypatch, feature):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    driver = importlib.import_module(f"software.recipes.{feature}.windows").WindowsDriver()
    def path_change(*args, verify_commands=None):
        if verify_commands:
            verify_commands()
    monkeypatch.setattr(driver, "_inject_path", path_change)
    monkeypatch.setattr(driver, "_remove_path", path_change)
    if hasattr(driver, "_remove_paths"):
        monkeypatch.setattr(driver, "_remove_paths", path_change)
    return driver


def _directory(driver, feature):
    if feature in ("python", "golang"):
        return importlib.import_module(f"software.recipes.{feature}.common").shim_dir()
    return driver._win_bin_dir()


@pytest.mark.parametrize("feature,last", [("python", "python3"), ("nodejs", "npx"),
                                           ("golang", "gofmt"), ("java", "keytool")])
@pytest.mark.parametrize("remove", [False, True])
def test_public_shim_refuses_foreign_file_without_partial_changes(tmp_path, monkeypatch, feature, last, remove):
    driver = _driver(tmp_path, monkeypatch, feature)
    driver.install_shim("")
    directory = _directory(driver, feature)
    foreign = directory / (last + ".cmd")
    foreign.write_bytes(b"administrator-owned\r\n")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(InstallError, match="unmanaged command"):
        driver.remove_shim() if remove else driver.install_shim("")
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.parametrize("feature", ["nodejs", "golang", "java"])
def test_known_snapshot_shims_migrate_to_command_specific_activity_routes(tmp_path, monkeypatch, feature):
    driver = _driver(tmp_path, monkeypatch, feature)
    directory = _directory(driver, feature)
    directory.mkdir(parents=True)
    constants = importlib.import_module(f"software.recipes.{feature}.constants")
    names, template = {
        "nodejs": (("node", "npm", "npx"), constants.__dict__.get("SHIM_NODE_CMD_TEMPLATE")),
        "golang": (("go", "gofmt"), constants.__dict__.get("SHIM_GO_CMD_TEMPLATE")),
        "java": (("java", "javac", "jar", "javadoc", "keytool"), constants.__dict__.get("SHIM_JAVA_CMD_TEMPLATE")),
    }[feature]
    for name in names:
        old = template if feature == "golang" else template.format(cmd=name)
        (directory / (name + ".cmd")).write_bytes(old.replace("\r\n", "\r\r\n").encode())
    driver.install_shim("")
    for name in names:
        body = (directory / (name + ".cmd")).read_bytes().decode()
        assert "findstr" not in body and "where " not in body
        assert '"%~dp0' in body and "DisableDelayedExpansion" in body
        assert name + (".cmd" if name in ("npm", "npx") else ".exe") in body
    driver.remove_shim()
    assert not list(directory.glob("*.cmd"))


def _transaction(tmp_path, names=("first", "second")):
    directory, root = tmp_path / "bin", tmp_path / "managed"
    root.mkdir(exist_ok=True)
    replacements = {name + ".cmd": commands.route_text(directory, root / (name + ".exe"), "test") for name in names}
    owned = lambda name, body: commands.route_owned(directory, name, body, "test", root)
    return directory, replacements, owned


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_publication_restores_all_files_and_created_directories(tmp_path, monkeypatch, cancel):
    directory, replacements, owned = _transaction(tmp_path)
    original = shell_path._publish_profile
    failed = []
    def partial(path, text, **kwargs):
        original(path, text, **kwargs)
        if path.name == "second.cmd" and not failed:
            failed.append(True)
            raise KeyboardInterrupt() if cancel else OSError("injected partial write")
    monkeypatch.setattr(shell_path, "_publish_profile", partial)
    with pytest.raises(KeyboardInterrupt if cancel else InstallError):
        with commands.command_transaction(directory, replacements, owned=owned):
            pytest.fail("failed publication entered callback")
    assert not directory.exists()


def test_callback_failure_restores_exact_legacy_bytes(tmp_path):
    directory, replacements, owned = _transaction(tmp_path)
    directory.mkdir()
    old = b"@echo off\r\r\nlegacy\r\r\n"
    for name in replacements:
        (directory / name).write_bytes(old)
    with pytest.raises(RuntimeError, match="PATH failed"):
        with commands.command_transaction(directory, replacements, owned=lambda name, text: text == old.decode() or owned(name, text)):
            raise RuntimeError("PATH failed")
    assert all((directory / name).read_bytes() == old for name in replacements)


def test_external_change_preserved_while_other_commands_are_compensated(tmp_path):
    directory, replacements, owned = _transaction(tmp_path)
    with pytest.raises(InstallError, match="rollback incomplete"):
        with commands.command_transaction(directory, replacements, owned=owned):
            (directory / "first.cmd").write_bytes(b"external replacement")
    assert (directory / "first.cmd").read_bytes() == b"external replacement"
    assert not (directory / "second.cmd").exists()


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "large", "binary"])
def test_command_path_rejects_links_and_unbounded_or_binary_files(tmp_path, kind):
    directory, replacements, owned = _transaction(tmp_path)
    directory.mkdir()
    path = directory / "second.cmd"
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    if kind == "hardlink":
        os.link(outside, path)
    elif kind == "symlink":
        path.symlink_to(outside)
    else:
        path.write_bytes(b"x" * (1024 * 1024 + 1) if kind == "large" else b"\xff")
    with pytest.raises(InstallError):
        with commands.command_transaction(directory, replacements, owned=owned):
            pytest.fail("unsafe command accepted")
    assert outside.read_bytes() == b"preserve"
    assert not (directory / "first.cmd").exists()


@pytest.mark.parametrize("feature", ["nodejs", "golang", "java"])
def test_fallback_path_failure_compensates_wrapper_group(tmp_path, monkeypatch, feature):
    driver = _driver(tmp_path, monkeypatch, feature)
    active = driver._active_junction()
    target = active.parent / "version"
    bin_dir = target if feature == "nodejs" else target / "bin"
    bin_dir.mkdir(parents=True)
    primary = {"nodejs": "node", "golang": "go", "java": "java"}[feature]
    (bin_dir / (primary + ".exe")).write_bytes(b"fixture; never executed")
    monkeypatch.setattr(driver, "_set_junction", lambda *args: False)
    def denied(*args, **kwargs):
        raise RuntimeError("PATH failed")
    monkeypatch.setattr(driver, "_inject_path", denied)
    with pytest.raises(RuntimeError, match="PATH failed"):
        driver.apply_version_link(str(bin_dir))
    assert not driver._win_bin_dir().exists()
    assert (bin_dir / (primary + ".exe")).exists()


def test_python_install_path_failure_leaves_no_shims(tmp_path, monkeypatch):
    driver = _driver(tmp_path, monkeypatch, "python")
    def denied(*args, **kwargs):
        raise RuntimeError("PATH failed")
    monkeypatch.setattr(driver, "_inject_path", denied)
    with pytest.raises(RuntimeError, match="PATH failed"):
        driver.install_shim("")
    assert not _directory(driver, "python").exists()


def test_routes_refuse_foreign_target_or_appended_marker_code(tmp_path):
    directory, replacements, owned = _transaction(tmp_path)
    assert not owned("first.cmd", commands.route_text(directory, tmp_path / "outside/first.exe", "test"))
    assert not owned("first.cmd", replacements["first.cmd"] + "echo injected\r\n")
    assert not owned("first.cmd", replacements["second.cmd"])


@pytest.mark.parametrize("name", ["../escape.cmd", "file:stream.cmd", "evil\n.cmd", "GO.cmd", "not.exe"])
def test_invalid_command_names_rejected_before_publication(tmp_path, name):
    directory, replacements, owned = _transaction(tmp_path)
    replacements[name] = replacements["first.cmd"]
    with pytest.raises(InstallError, match="invalid managed command name"):
        with commands.command_transaction(directory, replacements, owned=owned):
            pytest.fail("invalid name accepted")
    assert not directory.exists()


@pytest.mark.parametrize("feature", ["nodejs", "golang", "java"])
def test_restore_refuses_foreign_wrapper_before_junction_mutation(tmp_path, monkeypatch, feature):
    driver = _driver(tmp_path, monkeypatch, feature)
    directory = driver._win_bin_dir()
    directory.mkdir(parents=True)
    primary = {"nodejs": "node", "golang": "go", "java": "java"}[feature]
    foreign = directory / (primary + ".cmd")
    foreign.write_bytes(b"administrator wrapper")
    from software._shared import windows_junction
    def forbidden(*args):
        pytest.fail("junction mutated before ownership preflight")
    monkeypatch.setattr(windows_junction, "remove_junction", forbidden)
    with pytest.raises(InstallError, match="unmanaged command"):
        driver.restore_original()
    assert foreign.read_bytes() == b"administrator wrapper"


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_removal_restores_all_commands(tmp_path, monkeypatch, cancel):
    directory, replacements, owned = _transaction(tmp_path)
    with commands.command_transaction(directory, replacements, owned=owned):
        pass
    before = {name: (directory / name).read_bytes() for name in replacements}
    original, failed = Path.unlink, []
    def partial(path, *args, **kwargs):
        original(path, *args, **kwargs)
        if path == directory / "second.cmd" and not failed:
            failed.append(True)
            raise KeyboardInterrupt() if cancel else OSError("partial removal")
    monkeypatch.setattr(Path, "unlink", partial)
    with pytest.raises(KeyboardInterrupt if cancel else InstallError):
        with commands.command_transaction(directory, dict.fromkeys(replacements), owned=owned):
            pytest.fail("failed removal entered callback")
    assert {name: (directory / name).read_bytes() for name in replacements} == before


def test_managed_root_symlink_is_rejected_before_new_commands(tmp_path):
    directory = tmp_path / "bin"
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "managed"
    root.symlink_to(outside, target_is_directory=True)
    text = commands.route_text(directory, root / "first.exe", "test")
    with pytest.raises(InstallError, match="root must not be a link"):
        with commands.command_transaction(directory, {"first.cmd": text},
                owned=lambda name, body: commands.route_owned(directory, name, body, "test", root)):
            pytest.fail("linked root accepted")
    assert not directory.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows DACLs")
def test_published_commands_use_private_acl_and_profiles_keep_existing_acl(tmp_path):
    from core.secrets import restrict_permissions
    from core.windows_permissions import _descriptor
    directory, replacements, owned = _transaction(tmp_path)
    with commands.command_transaction(directory, replacements, owned=owned):
        pass
    reference = tmp_path / "private-reference"
    reference.write_bytes(b"reference")
    restrict_permissions(reference)
    expected, _ = _descriptor(reference)
    for name in replacements:
        descriptor, protected = _descriptor(directory / name)
        assert protected and descriptor.raw == expected.raw
    profile = tmp_path / "profile.ps1"
    profile.write_bytes(b"old profile")
    restrict_permissions(profile)
    before, protection = _descriptor(profile)
    shell_path._publish_profile(profile, "new profile")
    after, after_protection = _descriptor(profile)
    assert after.raw == before.raw and after_protection == protection


@pytest.mark.skipif(os.name != "nt", reason="requires Windows DACLs")
def test_acl_copy_failure_preserves_profile_and_cleans_temporary_file(tmp_path, monkeypatch):
    from core import windows_permissions
    profile = tmp_path / "profile.ps1"
    profile.write_bytes(b"preserve")
    def denied(*args):
        raise PermissionError("ACL copy failed")
    monkeypatch.setattr(windows_permissions, "copy_dacl", denied)
    with pytest.raises(PermissionError, match="ACL copy failed"):
        shell_path._publish_profile(profile, "replacement")
    assert profile.read_bytes() == b"preserve"
    assert not list(tmp_path.glob(".opskit-profile-*"))


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows cmd and junctions")
@pytest.mark.parametrize("fallback", [False, True])
def test_native_node_batch_route_handles_unicode_percent_home_and_delayed_expansion(tmp_path, monkeypatch, fallback):
    home = tmp_path / "account 中文 & %OPSKIT_UNSET% !"
    home.mkdir()
    driver = _driver(home, monkeypatch, "nodejs")
    target = driver._active_junction().parent / "node1"
    target.mkdir(parents=True)
    system = Path(os.environ["SystemRoot"]) / "System32"
    shutil.copyfile(system / "where.exe", target / "node.exe")
    for name in ("npm", "npx"):
        (target / (name + ".cmd")).write_bytes(f"@echo off\r\necho {name}-target:%~1\r\nexit /b 7\r\n".encode())
    if fallback:
        monkeypatch.setattr(driver, "_set_junction", lambda *args: False)
    else:
        driver.install_shim("")
    driver.apply_version_link(str(target))
    for name in ("npm", "npx"):
        result = subprocess.run([str(system / "cmd.exe"), "/d", "/v:on", "/c", name + ".cmd", "A!B"],
                                cwd=driver._win_bin_dir(), capture_output=True, text=True, timeout=10)
        assert result.returncode == 7, result.stderr
        assert result.stdout.strip() == name + "-target:A!B"
    result = subprocess.run([str(system / "cmd.exe"), "/d", "/c", "node.cmd", "opskit-no-such-command-123"],
                            cwd=driver._win_bin_dir(), capture_output=True, timeout=10)
    assert result.returncode == 1


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows command executables")
def test_native_go_and_gofmt_shims_route_to_different_commands(tmp_path, monkeypatch):
    driver = _driver(tmp_path, monkeypatch, "golang")
    target = driver._active_junction().parent / "go1"
    (target / "bin").mkdir(parents=True)
    system = Path(os.environ["SystemRoot"]) / "System32"
    shutil.copyfile(system / "where.exe", target / "bin/go.exe")
    shutil.copyfile(system / "findstr.exe", target / "bin/gofmt.exe")
    driver.install_shim("")
    driver.apply_version_link(str(target / "bin"))
    needle = b"opskit-content-4e679a123957"
    for name, status in (("go", 1), ("gofmt", 0)):
        result = subprocess.run([str(system / "cmd.exe"), "/d", "/c", name + ".cmd", needle.decode()],
                                cwd=_directory(driver, "golang"), input=needle + b"\r\n",
                                capture_output=True, timeout=10)
        assert result.returncode == status
        if name == "gofmt":
            assert result.stdout.strip() == needle
