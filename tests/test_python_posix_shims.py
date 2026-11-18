"""Unknown Python wrappers survive install/remove; known groups compensate."""
from pathlib import Path
import stat
import os

import pytest

from software.base import InstallError
from software._shared.activation import ActivationRollbackError
from software._shared import shell_path
from software.recipes.python import common, linux, posix_shims


@pytest.fixture
def shims(tmp_path, monkeypatch):
    directory = tmp_path / "shims"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(common, "shim_dir", lambda: directory)
    monkeypatch.setattr(linux, "PROFILE_D_SHIM_FILE", str(tmp_path / "system-profile"))
    monkeypatch.setattr(shell_path, "_is_root", lambda: False)
    driver = linux.LinuxDriver()
    monkeypatch.setattr(driver, "_symlink_target", lambda: (str(tmp_path / "active-python"), False))
    return driver, directory, str(tmp_path / "fallback with ' quote")


@pytest.mark.parametrize("remove", [False, True])
def test_foreign_last_file_rejected_before_any_changes(shims, remove):
    driver, directory, fallback = shims
    directory.mkdir()
    (directory / "python").write_bytes(b"administrator wrapper")
    with pytest.raises(InstallError, match="unmanaged"):
        driver.remove_shim() if remove else driver.install_shim(fallback)
    assert not (directory / "python3").exists()
    assert (directory / "python").read_bytes() == b"administrator wrapper"


@pytest.mark.parametrize("remove", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_late_profile_failure_compensates_entire_group(shims, monkeypatch, remove, cancel):
    driver, directory, fallback = shims
    driver.install_shim(fallback)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    def fail(*args):
        raise KeyboardInterrupt() if cancel else PermissionError("profile failure")
    monkeypatch.setattr(shell_path, "remove_rc_path" if remove else "inject_rc_path", fail)
    with pytest.raises(KeyboardInterrupt if cancel else PermissionError):
        driver.remove_shim() if remove else driver.install_shim(fallback + "-new")
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_new_wrapper_executable_and_fallback_safely_quoted(shims):
    driver, directory, fallback = shims
    driver.install_shim(fallback)
    for path in directory.iterdir():
        if os.name != "nt":
            assert path.stat().st_mode & stat.S_IXUSR
        body = path.read_text(encoding="utf-8")
        assert posix_shims.owned(body, driver._symlink_target()[0])
        assert "sed " not in body and "python.json" not in body
    driver.remove_shim()
    assert not directory.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires a native POSIX shell")
def test_real_quoted_fallback_and_literal_arguments(shims, tmp_path):
    from core.process import capture
    driver, directory, fallback = shims
    target = Path(fallback)
    target.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    target.chmod(0o755)
    driver.install_shim(fallback)
    arguments = ["two words", "semi;literal", "$(printf unsafe)"]
    result = capture([str(directory / "python3"), *arguments], timeout=5, max_bytes=4096,
                     env=dict(os.environ, HOME=str(tmp_path)))
    assert result.returncode == 0
    assert result.stdout.splitlines() == arguments


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_legacy_known_wrapper_migrates_to_static_route(shims, line_ending):
    from software.recipes.python.constants import LEGACY_SHIM_SH_TEMPLATE
    import shlex
    driver, directory, fallback = shims
    directory.mkdir()
    for name in ("python3", "python"):
        body = LEGACY_SHIM_SH_TEMPLATE.format(fallback=shlex.quote(fallback)).replace("\n", line_ending)
        (directory / name).write_bytes(body.encode("utf-8"))
    driver.install_shim(fallback)
    assert all("python.json" not in path.read_text(encoding="utf-8") for path in directory.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX shell links")
def test_active_route_switches_without_snapshot_or_wrapper_rewrite(shims, tmp_path):
    from core.process import capture
    driver, directory, fallback = shims
    route, _ = driver._symlink_target()
    first, second = tmp_path / "first", tmp_path / "second"
    for path, value in ((first, "first"), (second, "second")):
        path.write_text(f'#!/bin/sh\nprintf "{value}\\n"\n', encoding="utf-8")
        path.chmod(0o755)
    Path(route).symlink_to(first)
    driver.install_shim(fallback)
    wrapper = directory / "python3"
    original = wrapper.read_bytes()
    env = dict(os.environ, HOME=str(tmp_path))
    assert capture([str(wrapper)], env=env, timeout=5).stdout.strip() == "first"
    driver.apply_version_link(str(second))
    assert capture([str(wrapper)], env=env, timeout=5).stdout.strip() == "second"
    assert wrapper.read_bytes() == original
    assert not (tmp_path / ".opskit/snapshots/python.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX file modes")
@pytest.mark.parametrize("failure", [None, "failure", "cancel"])
def test_owned_nonexecutable_wrapper_repaired_and_mode_compensated(shims, monkeypatch, failure):
    driver, directory, fallback = shims
    driver.install_shim(fallback)
    for path in directory.iterdir():
        path.chmod(0o600)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    if failure:
        def fail(*args):
            raise KeyboardInterrupt() if failure == "cancel" else PermissionError("later profile failure")
        monkeypatch.setattr(shell_path, "inject_rc_path", fail)
        with pytest.raises(KeyboardInterrupt if failure == "cancel" else PermissionError):
            driver.install_shim(fallback)
    else:
        driver.install_shim(fallback)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert all(stat.S_IMODE(path.stat().st_mode) == (0o600 if failure else 0o700) for path in directory.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX file modes")
def test_external_permission_change_preserved_other_wrapper_compensated(shims, monkeypatch):
    driver, directory, fallback = shims
    driver.install_shim(fallback)
    for path in directory.iterdir():
        path.chmod(0o600)
    def interfere(*args):
        (directory / "python").chmod(0o640)
        raise PermissionError("later failure")
    monkeypatch.setattr(shell_path, "inject_rc_path", interfere)
    with pytest.raises(ActivationRollbackError, match="rollback incomplete"):
        driver.install_shim(fallback)
    assert stat.S_IMODE((directory / "python").stat().st_mode) == 0o640
    assert stat.S_IMODE((directory / "python3").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX file modes")
def test_special_permission_bits_rejected_before_mutation(shims):
    driver, directory, fallback = shims
    driver.install_shim(fallback)
    path = directory / "python"
    path.chmod(0o4600)
    before = path.read_bytes()
    with pytest.raises(InstallError, match="special permission bits"):
        driver.install_shim(fallback)
    assert path.read_bytes() == before and stat.S_IMODE(path.stat().st_mode) == 0o4600


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX file modes")
@pytest.mark.parametrize("after_publication", [False, True])
def test_mode_only_partial_publication_failure_restores_group(shims, monkeypatch, after_publication):
    driver, directory, fallback = shims
    driver.install_shim(fallback)
    for path in directory.iterdir():
        path.chmod(0o600)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    publish = shell_path._publish_profile
    failed = False

    def fail_second(path, text, **kwargs):
        nonlocal failed
        if path == directory / "python" and not failed:
            failed = True
            if after_publication:
                publish(path, text, **kwargs)
            raise PermissionError("second shim publication failure")
        return publish(path, text, **kwargs)

    monkeypatch.setattr(shell_path, "_publish_profile", fail_second)
    with pytest.raises(PermissionError, match="second shim publication failure"):
        driver.install_shim(fallback)
    assert failed
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
