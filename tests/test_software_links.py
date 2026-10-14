"""Failed filesystem mutations must preserve original commands and report failure."""
import os
from pathlib import Path

import pytest

from software._shared import shell_path
from software._shared.snapshot import SnapshotStore


@pytest.fixture
def system_bin(tmp_path, monkeypatch):
    folder = tmp_path / "system-bin"
    folder.mkdir()
    monkeypatch.setattr(shell_path, "SYSTEM_BIN_DIR", str(folder))
    monkeypatch.setattr(shell_path, "_is_root", lambda: True)
    return folder


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise


def test_link_replace_failure_preserves_previous_entry(system_bin, tmp_path, monkeypatch):
    old = tmp_path / "old"
    old.write_text("old")
    _symlink(system_bin / "tool", old)
    version = tmp_path / "new"
    version.mkdir()
    (version / "tool").write_text("new")

    def fail(*args):
        raise PermissionError("injected replace failure")

    monkeypatch.setattr(shell_path.os, "replace", fail)
    with pytest.raises(PermissionError):
        shell_path.link_into_system_bin(str(version), ["tool"], managed_root=tmp_path)
    assert (system_bin / "tool").resolve() == old
    assert list(system_bin.iterdir()) == [system_bin / "tool"]


def test_existing_regular_command_is_not_deleted(system_bin, tmp_path):
    (system_bin / "tool").write_text("user command")
    (tmp_path / "tool").write_text("new")
    with pytest.raises(FileExistsError):
        shell_path.link_into_system_bin(str(tmp_path), ["tool"], managed_root=tmp_path)
    assert (system_bin / "tool").read_text() == "user command"


@pytest.mark.parametrize("dangling", [False, True])
def test_unmanaged_link_conflict_is_rejected_before_any_publication(system_bin, tmp_path, dangling):
    root = tmp_path / "managed"
    root.mkdir()
    for name in ("first", "second"):
        (root / name).write_text("new")
    unrelated = tmp_path / "administrator-tool"
    if not dangling:
        unrelated.write_text("original")
    link = system_bin / "second"
    _symlink(link, unrelated)
    previous_target = link.readlink()
    with pytest.raises(FileExistsError, match="unmanaged command link"):
        shell_path.link_into_system_bin(str(root), ["first", "second"], managed_root=root)
    assert not (system_bin / "first").exists()
    assert link.is_symlink()
    assert link.readlink() == previous_target


def test_managed_link_can_switch_versions(system_bin, tmp_path):
    root = tmp_path / "managed"
    old, new = root / "old", root / "new"
    old.mkdir(parents=True)
    new.mkdir()
    (old / "tool").write_text("old")
    (new / "tool").write_text("new")
    _symlink(system_bin / "tool", old / "tool")
    shell_path.link_into_system_bin(str(new), ["tool"], managed_root=root)
    assert (system_bin / "tool").resolve() == new / "tool"


def test_source_link_cannot_publish_command_outside_managed_root(system_bin, tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    _symlink(root / "tool", outside)
    with pytest.raises(ValueError):
        shell_path.link_into_system_bin(str(root), ["tool"], managed_root=root)
    assert list(system_bin.iterdir()) == []


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_late_publication_failure_restores_entire_command_group(system_bin, tmp_path, monkeypatch, existing, cancel):
    root = tmp_path / "managed"
    old, new = root / "old", root / "new"
    old.mkdir(parents=True)
    new.mkdir()
    previous = {}
    for name in ("first", "second"):
        (old / name).write_text("old")
        (new / name).write_text("new")
        if existing:
            _symlink(system_bin / name, old / name)
            previous[name] = (system_bin / name).readlink()
    original = shell_path.os.replace

    def fail_second(source, dest):
        if dest == system_bin / "second":
            raise KeyboardInterrupt() if cancel else PermissionError("second publication denied")
        return original(source, dest)

    monkeypatch.setattr(shell_path.os, "replace", fail_second)
    with pytest.raises(KeyboardInterrupt if cancel else PermissionError):
        shell_path.link_into_system_bin(str(new), ["first", "second"], managed_root=root)
    for name in ("first", "second"):
        link = system_bin / name
        if existing:
            assert link.readlink() == previous[name]
        else:
            assert not link.is_symlink() and not link.exists()
    assert not list(system_bin.glob(".opskit-link-*"))


def test_rollback_preserves_external_command_change(system_bin, tmp_path, monkeypatch):
    from software.base import InstallError
    root = tmp_path / "managed"
    root.mkdir()
    for name in ("first", "second"):
        (root / name).write_text("new")
    original = shell_path.os.replace

    def interfere(source, dest):
        if dest == system_bin / "second":
            first = system_bin / "first"
            first.unlink()
            first.write_text("administrator replacement")
            raise PermissionError("publication denied")
        return original(source, dest)

    monkeypatch.setattr(shell_path.os, "replace", interfere)
    with pytest.raises(InstallError, match="rollback incomplete"):
        shell_path.link_into_system_bin(str(root), ["first", "second"], managed_root=root)
    assert (system_bin / "first").read_text() == "administrator replacement"
    assert not (system_bin / "second").exists()
    assert not list(system_bin.glob(".opskit-link-*"))


def test_uninstall_does_not_match_similarly_named_directory(system_bin, tmp_path):
    root = tmp_path / "versions"
    root.mkdir()
    similar = tmp_path / "versions-unrelated"
    similar.mkdir()
    (similar / "tool").write_text("user command")
    _symlink(system_bin / "tool", similar / "tool")
    shell_path.unlink_system_bin(["tool"], root)
    assert (system_bin / "tool").is_symlink()
    assert (system_bin / "tool").read_text() == "user command"


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("dangling", [False, True])
def test_failed_group_removal_restores_original_links(system_bin, tmp_path, monkeypatch, cancel, dangling):
    root = tmp_path / "managed"
    root.mkdir()
    previous = {}
    for name in ("first", "second"):
        if not dangling:
            (root / name).write_text("original")
        _symlink(system_bin / name, root / name)
        previous[name] = (system_bin / name).readlink()
    original = Path.unlink

    def fail_second(path, *args, **kwargs):
        if path == system_bin / "second":
            raise KeyboardInterrupt() if cancel else PermissionError("second removal denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(KeyboardInterrupt if cancel else PermissionError):
        shell_path.unlink_system_bin(["first", "second"], root)
    assert {name: (system_bin / name).readlink() for name in previous} == previous
    assert not list(system_bin.glob(".opskit-link-*"))


def test_invalid_late_removal_name_does_not_delete_earlier_link(system_bin, tmp_path):
    _symlink(system_bin / "tool", tmp_path / "tool")
    previous = (system_bin / "tool").readlink()
    with pytest.raises(ValueError, match="invalid command link name"):
        shell_path.unlink_system_bin(["tool", ".."], tmp_path)
    assert (system_bin / "tool").readlink() == previous


@pytest.mark.parametrize("remove", [False, True])
def test_link_changed_after_ownership_read_is_preserved(system_bin, tmp_path, monkeypatch, remove):
    root = tmp_path / "managed"
    root.mkdir()
    (root / "tool").write_text("managed")
    outside = tmp_path / "outside"
    outside.write_text("administrator")
    link = system_bin / "tool"
    _symlink(link, root / "tool")
    original = Path.readlink
    changed = []

    def swap_after_read(path):
        target = original(path)
        if path == link and not changed:
            changed.append(True)
            link.unlink()
            link.symlink_to(outside)
        return target

    monkeypatch.setattr(Path, "readlink", swap_after_read)
    with pytest.raises(ValueError, match="changed before publication"):
        if remove:
            shell_path.unlink_system_bin(["tool"], root)
        else:
            shell_path.link_into_system_bin(str(root), ["tool"], managed_root=root)
    assert link.resolve() == outside
    assert link.read_text() == "administrator"


def test_failed_removal_preserves_external_file_and_reports_incomplete(system_bin, tmp_path, monkeypatch):
    from software.base import InstallError
    for name in ("first", "second"):
        _symlink(system_bin / name, tmp_path / name)
    original = Path.unlink

    def interfere(path, *args, **kwargs):
        if path == system_bin / "second":
            (system_bin / "first").write_text("administrator command")
            raise PermissionError("second removal denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interfere)
    with pytest.raises(InstallError, match="rollback incomplete"):
        shell_path.unlink_system_bin(["first", "second"], tmp_path)
    assert (system_bin / "first").read_text() == "administrator command"
    assert (system_bin / "second").is_symlink()


def test_managed_link_removed_and_permission_failure_propagates(system_bin, tmp_path, monkeypatch):
    root = tmp_path / "managed"
    root.mkdir()
    target = root / "tool"
    target.write_text("tool")
    link = system_bin / "tool"
    _symlink(link, target)
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path == link:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(PermissionError):
        shell_path.unlink_system_bin(["tool"], root)
    assert link.is_symlink()
    monkeypatch.setattr(Path, "unlink", original)
    shell_path.unlink_system_bin(["tool"], root)
    assert not link.is_symlink()


def test_snapshot_delete_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store = SnapshotStore("snapshots", "test.json")
    store.path.parent.mkdir()
    store.path.write_text("{}")
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path == store.path:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(PermissionError):
        store.delete()
    assert store.path.read_text() == "{}"


def test_path_prepend_moves_existing_later_entry_first(monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(["system", "shims", "other", "shims"]))
    shell_path.prepend_process_path("shims")
    assert os.environ["PATH"].split(os.pathsep) == ["shims", "system", "other"]


def test_missing_installed_binary_is_not_success(tmp_path):
    from software.recipes.nodejs.recipe import NodeRecipe
    from software.base import InstallError
    with pytest.raises(InstallError, match="unavailable"):
        NodeRecipe()._verify_runnable(str(tmp_path))
