"""Bootstrap and launch state transitions using small private runtime trees."""

import os
import signal

import pytest

from core import directory_install, directory_updates
from core.directory_install import APP_NAME, Install, InstallError, bootstrap
from scripts import directory_launcher
from scripts.directory_package import create_package


@pytest.fixture
def release(tmp_path, monkeypatch):
    launcher = tmp_path / "downloaded-launcher"
    launcher.write_bytes(b"launcher v1")
    target = tmp_path / "installed"
    target.mkdir()
    install = Install(target / APP_NAME)
    packages = []
    for version in (1, 2):
        tree = tmp_path / f"app-v{version}"
        tree.mkdir()
        executable = tree / APP_NAME
        executable.write_bytes(f"app v{version}".encode("ascii"))
        executable.chmod(0o755)
        package = tmp_path / f"v{version}.tar.xz"
        create_package(tree, package, version)
        packages.append(package)
    monkeypatch.setattr(Install, "healthy", lambda self, generation: True)
    return launcher, install, *packages


@pytest.fixture
def installed(release):
    launcher, install, v1, v2 = release
    bootstrap(launcher, v1, install.entry, 1)
    return launcher, install, v1, v2


def test_bootstrap_keeps_previous_generation(installed):
    launcher, install, v1, v2 = installed
    first = install.state().active
    launcher.write_bytes(b"launcher v2")
    bootstrap(launcher, v2, install.entry, 2)
    state = install.state()
    assert state.active.version == 2
    assert state.previous == first
    assert install.executable(first).read_bytes() == b"app v1"
    assert install.executable(state.active).read_bytes() == b"app v2"
    assert install.entry.read_bytes() == b"launcher v2"
    with pytest.raises(InstallError):
        bootstrap(launcher, v1, install.entry, 1)
    assert install.state() == state


def test_failed_bootstrap_does_not_replace_launcher_or_state(installed, monkeypatch):
    launcher, install, _, v2 = installed
    first = install.state()
    launcher.write_bytes(b"launcher v2")
    monkeypatch.setattr(Install, "healthy", lambda self, generation: False)
    with pytest.raises(InstallError, match="startup"):
        bootstrap(launcher, v2, install.entry, 2)
    assert install.entry.read_bytes() == b"launcher v1"
    assert install.state() == first
    assert {path.name for path in install.root.glob("g-*")} == {first.active.name}


def test_bootstrap_keeps_selected_tree_when_state_sync_fails(installed, monkeypatch):
    launcher, install, _, v2 = installed
    first = install.state().active
    sync = directory_install._sync_directory

    def fail_after_pointer_replacement(path):
        if path == install.root and install.state().active.version == 2:
            raise OSError("simulated state-directory sync failure")
        sync(path)

    monkeypatch.setattr(directory_install, "_sync_directory", fail_after_pointer_replacement)
    with pytest.raises(OSError, match="state-directory sync failure"):
        bootstrap(launcher, v2, install.entry, 2)
    selected = install.state()
    assert selected.previous == first
    assert install.executable(first).read_bytes() == b"app v1"
    assert install.executable(selected.active).read_bytes() == b"app v2"


def test_fresh_bootstrap_keeps_entry_and_tree_when_entry_sync_fails(release, monkeypatch):
    launcher, install, v1, _ = release
    sync = directory_install._sync_directory

    def fail_after_entry_replacement(path):
        if path == install.entry.parent:
            raise OSError("simulated launcher-directory sync failure")
        sync(path)

    monkeypatch.setattr(directory_install, "_sync_directory", fail_after_entry_replacement)
    with pytest.raises(OSError, match="launcher-directory sync failure"):
        bootstrap(launcher, v1, install.entry, 1)
    assert install.entry.read_bytes() == launcher.read_bytes()
    assert install.executable(install.state().active).read_bytes() == b"app v1"


def test_bootstrap_restores_previous_state_if_launcher_replacement_fails(installed, monkeypatch):
    launcher, install, _, v2 = installed
    first = install.state()
    replace = os.replace

    def refuse_launcher_replacement(source, target):
        if target == install.entry:
            raise OSError("simulated launcher replacement failure")
        replace(source, target)

    monkeypatch.setattr(os, "replace", refuse_launcher_replacement)
    with pytest.raises(OSError, match="launcher replacement failure"):
        bootstrap(launcher, v2, install.entry, 2)
    assert install.entry.read_bytes() == launcher.read_bytes()
    assert install.state() == first


def test_bootstrap_refuses_launcher_replaced_during_probe(installed, monkeypatch):
    launcher, install, _, v2 = installed
    first = install.state()

    def replace_during_probe(self, generation):
        other = install.entry.with_name("other-launcher")
        other.write_bytes(b"external replacement")
        other.replace(install.entry)
        return True

    monkeypatch.setattr(Install, "healthy", replace_during_probe)
    with pytest.raises(InstallError, match="launcher changed"):
        bootstrap(launcher, v2, install.entry, 2)
    assert install.entry.read_bytes() == b"external replacement"
    assert install.state() == first


def test_unhealthy_staged_generation_keeps_active_install(installed, monkeypatch):
    _, install, _, v2 = installed
    state = install.state()
    candidate = install.prepare(v2, 2)
    state.staged = candidate
    state.write(install.state_path)
    monkeypatch.setattr(Install, "healthy", lambda self, generation: generation != candidate)
    with pytest.raises(RuntimeError):
        directory_updates.activate(install, expected_candidate_id=candidate.name)
    result = install.state()
    assert result.active == state.active
    assert result.staged is None
    assert result.rejected == 2
    assert not (install.root / candidate.name).exists()


def test_damaged_staged_generation_is_rejected_before_health_check(installed, monkeypatch):
    _, install, _, v2 = installed
    state = install.state()
    candidate = install.prepare(v2, 2)
    state.staged = candidate
    state.write(install.state_path)
    install.executable(candidate).write_bytes(b"changed")

    def unexpected_probe(*args):
        pytest.fail("candidate integrity must be checked before starting the executable")

    monkeypatch.setattr(Install, "healthy", unexpected_probe)
    with pytest.raises(RuntimeError):
        directory_updates.activate(install, expected_candidate_id=candidate.name)
    result = install.state()
    assert result.active == state.active and result.rejected == 2


def test_next_menu_startup_applies_an_interrupted_directory_update(installed, monkeypatch):
    from core import updater

    _, install, _, v2 = installed
    state = install.state()
    state.staged = install.prepare(v2, 2)
    state.write(install.state_path)
    monkeypatch.setattr(directory_install, "managed_install", lambda: install)

    assert not updater.check_and_apply_pending(post_update=True)
    assert install.state() == state
    assert updater.check_and_apply_pending()
    selected = install.state()
    assert selected.active == state.staged and selected.previous == state.active
    assert not updater.check_and_apply_pending()


class _Child:
    def __init__(self, code):
        self.code = code

    def wait(self):
        return self.code


@pytest.mark.parametrize("already_rejected", [None, 3])
def test_launcher_rolls_back_to_healthy_generation(installed, monkeypatch, already_rejected):
    launcher, install, _, v2 = installed
    bootstrap(launcher, v2, install.entry, 2)
    state = install.state()
    state.rejected = already_rejected
    state.write(install.state_path)
    launched = []
    monkeypatch.setattr(Install, "healthy", lambda self, generation: generation.version == 1)
    monkeypatch.setattr(directory_launcher, "reset_dll_directory", lambda: None)

    def popen(arguments, *, env):
        launched.append(arguments)
        assert env[directory_install.ENTRY_ENV] == str(install.entry)
        return _Child(0)

    monkeypatch.setattr(directory_launcher.subprocess, "Popen", popen)
    arguments = ["--non-interactive", "plugin", "list"]
    assert directory_launcher.launch(install, arguments) == 0
    assert launched == [[str(install.executable(state.previous)), *arguments]]
    recovered = install.state()
    assert recovered.active == state.previous
    assert recovered.previous is None
    assert recovered.rejected == max(2, already_rejected or 0)
    assert install.executable(state.active).is_file()


@pytest.mark.parametrize("code", [42, -signal.SIGTERM])
def test_launcher_propagates_command_error_without_rollback(code, installed, monkeypatch):
    launcher, install, _, v2 = installed
    bootstrap(launcher, v2, install.entry, 2)
    state = install.state()
    launched = []
    monkeypatch.setattr(directory_launcher, "reset_dll_directory", lambda: None)

    def popen(arguments, *, env):
        launched.append(arguments)
        return _Child(code)

    monkeypatch.setattr(directory_launcher.subprocess, "Popen", popen)
    arguments = ["software", "install", "example"]
    expected = 128 - code if os.name != "nt" and code < 0 else code
    assert directory_launcher.launch(install, arguments) == expected
    assert launched == [[str(install.executable(state.active)), *arguments]]
    assert install.state() == state
