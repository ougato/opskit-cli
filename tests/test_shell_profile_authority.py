"""Shell mutations must surface failures and preserve unmanaged system files."""
from pathlib import Path
import os

import pytest

from software._shared import shell_path


@pytest.fixture
def profile_context(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    profile = tmp_path / "profile.d" / "opskit-tool.sh"
    profile.parent.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(shell_path, "_is_root", lambda: True)
    return home, profile


def test_profile_publication_permission_failure_is_not_silent(profile_context, monkeypatch):
    home, profile = profile_context
    replace = os.replace
    def fail(source, target):
        if Path(target) == profile:
            raise PermissionError("owned fixture publication denied")
        return replace(source, target)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        shell_path.inject_rc_path(str(home / "shims"), "# begin", "# end", str(profile))
    assert not profile.exists()


def test_existing_unmanaged_system_profile_is_preserved(profile_context):
    home, profile = profile_context
    profile.write_text("# administrator-owned settings\nexport PATH=/trusted/bin:$PATH\n")
    previous = profile.read_bytes()
    with pytest.raises(FileExistsError):
        shell_path.inject_rc_path(str(home / "shims"), "# begin", "# end", str(profile))
    assert profile.read_bytes() == previous


def test_nonroot_removal_does_not_delete_system_profile(profile_context, monkeypatch):
    _, profile = profile_context
    profile.write_text("# administrator-owned settings\n")
    previous = profile.read_bytes()
    monkeypatch.setattr(shell_path, "_is_root", lambda: False)
    shell_path.remove_rc_path("# begin", "# end", str(profile))
    assert profile.read_bytes() == previous


def test_rc_read_failure_is_not_reported_as_success(profile_context, monkeypatch):
    home, _ = profile_context
    rc = home / ".bashrc"
    rc.write_text("# begin\nexport PATH=/old/bin:$PATH\n# end\n")
    original = rc.read_bytes()
    read = Path.open
    def fail(path, *args, **kwargs):
        if path == rc:
            raise PermissionError("owned fixture read denied")
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(PermissionError):
        shell_path.remove_rc_path("# begin", "# end")
    with read(rc, "rb") as source:
        assert source.read() == original


@pytest.mark.parametrize("operation", ["install", "remove"])
def test_python_linux_uses_shared_profile_failure_boundary(profile_context, monkeypatch, operation):
    from software.recipes.python import common, linux
    home, profile = profile_context
    monkeypatch.setattr(common, "shim_dir", lambda: home / "shims")
    monkeypatch.setattr(linux, "PROFILE_D_SHIM_FILE", str(profile))
    def denied(*args, **kwargs):
        raise PermissionError("shared profile operation denied")
    monkeypatch.setattr(shell_path, "inject_rc_path" if operation == "install" else "remove_rc_path", denied)
    driver = linux.LinuxDriver()
    with pytest.raises(PermissionError, match="shared profile operation denied"):
        if operation == "install":
            driver.install_shim("/known/fallback/python")
        else:
            driver.remove_shim()


def test_late_profile_failure_restores_all_earlier_rc_bytes(profile_context, monkeypatch):
    home, profile = profile_context
    rc = home / ".bashrc"
    original = b"# preserve CRLF\r\nexport OTHER=1\r\n"
    rc.write_bytes(original)
    rc.chmod(0o640)
    mode = rc.stat().st_mode
    replace = os.replace
    def fail(source, target):
        if Path(target) == profile:
            raise PermissionError("owned profile publication denied")
        return replace(source, target)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        shell_path.inject_rc_path(str(home / "shims"), "# begin", "# end", str(profile))
    assert rc.read_bytes() == original and rc.stat().st_mode == mode
    assert not profile.exists()
    assert not list(home.glob(".opskit-profile-*"))
    assert not list(profile.parent.glob(".opskit-profile-*"))


def test_owned_profile_is_idempotent_and_removed_without_unrelated_rc_loss(profile_context):
    home, profile = profile_context
    rc = home / ".bashrc"
    rc.write_text("# unrelated\n")
    args = str(home / "shim directory"), "# begin", "# end", str(profile)
    shell_path.inject_rc_path(*args)
    before = rc.read_bytes(), profile.read_bytes()
    shell_path.inject_rc_path(*args)
    assert (rc.read_bytes(), profile.read_bytes()) == before
    shell_path.remove_rc_path(*args[1:])
    assert rc.read_text() == "# unrelated\n" and not profile.exists()


def test_hardlinked_profile_is_rejected_before_other_rc_changes(profile_context, tmp_path):
    home, profile = profile_context
    target = tmp_path / "administrator-file"
    target.write_text("# original\n")
    os.link(target, profile)
    rc = home / ".bashrc"
    rc.write_text("# unchanged\n")
    with pytest.raises(ValueError, match="regular file"):
        shell_path.inject_rc_path(str(home / "shims"), "# begin", "# end", str(profile))
    assert target.read_text() == "# original\n" and rc.read_text() == "# unchanged\n"


def test_malformed_marker_does_not_discard_rc_tail(profile_context):
    home, _ = profile_context
    rc = home / ".bashrc"
    original = "# begin\n# unrelated tail\n"
    rc.write_text(original)
    with pytest.raises(ValueError, match="ambiguous"):
        shell_path.remove_rc_path("# begin", "# end")
    assert rc.read_text() == original
