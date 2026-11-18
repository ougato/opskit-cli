"""Native link guards and compensation across dependent operations."""
import os
from pathlib import Path

import pytest

from software._shared import windows_junction as junction
from software.base import InstallError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows reparse APIs")


@pytest.mark.parametrize("mode", ["create", "switch", "remove"])
@pytest.mark.parametrize("cancel", [False, True])
def test_explicit_external_targets_preserve_transaction_compensation(tmp_path, mode, cancel):
    root = tmp_path / "managed"
    root.mkdir()
    first, second, third = _targets(tmp_path)
    targets = (first, second)
    entry = root / "active"
    if mode != "create":
        junction.set_junction(entry, first, root, authorized_targets=targets)
    with pytest.raises(KeyboardInterrupt if cancel else RuntimeError):
        with junction.junction_transaction(entry, root, authorized_targets=targets) as change:
            if mode == "remove":
                junction.remove_junction(entry, root, authorized_targets=targets)
                change.checkpoint(removed=True)
            else:
                junction.set_junction(entry, second, root, authorized_targets=targets)
                change.checkpoint(second)
            raise KeyboardInterrupt() if cancel else RuntimeError("dependent failure")
    if mode == "create":
        assert not os.path.lexists(entry)
    else:
        assert entry.resolve() == first
        junction.remove_junction(entry, root, authorized_targets=targets)
    assert all((target / "marker").read_text() == "preserved" for target in (first, second, third))


def test_explicit_targets_do_not_authorize_descendants_or_unknown_existing_link(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    first, second, third = _targets(tmp_path)
    child = first / "child"
    child.mkdir()
    entry = root / "active"
    with pytest.raises(InstallError, match="explicitly authorized"):
        junction.set_junction(entry, child, root, authorized_targets=(first,))
    junction.set_junction(entry, first, root, authorized_targets=(first,))
    for operation in (
        lambda: junction.set_junction(entry, second, root, authorized_targets=(second,)),
        lambda: junction.remove_junction(entry, root, authorized_targets=(second,)),
    ):
        with pytest.raises(InstallError, match="explicitly authorized"):
            operation()
        assert entry.resolve() == first
    junction.remove_junction(entry, root, authorized_targets=(first,))


def test_explicit_cross_volume_python_root_and_raw_arguments(tmp_path):
    import json
    import subprocess
    import sys
    from software._shared.windows_commands import route_text
    target = Path(sys.base_prefix).resolve()
    if target.drive.lower() == tmp_path.drive.lower():
        pytest.skip("fixture needs distinct Windows volumes")
    directory = tmp_path / "Unicode 中文 & ! %"
    directory.mkdir()
    entry = directory / ".python-current"
    command = directory / "python.cmd"
    probe = directory / "probe.py"
    probe.write_text('import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(37)', encoding="utf-8")
    arguments = ["中文 空格", "literal & value", "percent%", "bang!", 'quote"inside', "trailing\\"]
    junction.set_junction(entry, target, directory, authorized_targets=(target,))
    try:
        command.write_bytes(route_text(directory, entry / "python.exe", "python").encode("ascii"))
        line = '"' + str(command) + '" "' + str(probe) + '" ' + subprocess.list2cmdline(arguments)
        result = subprocess.run('cmd.exe /d /v:off /s /c "' + line + '"',
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert result.returncode == 37, result.stderr
        assert json.loads(result.stdout) == arguments
    finally:
        junction.remove_junction(entry, directory, authorized_targets=(target,))
    assert not os.path.lexists(entry)
    assert (target / "python.exe").is_file()


def _targets(tmp_path):
    first, second, third = (tmp_path / name for name in ("first", "second", "third"))
    for directory in (first, second, third):
        directory.mkdir()
        (directory / "marker").write_text("preserved")
    return first, second, third


@pytest.mark.parametrize("mode", ["create", "switch", "remove"])
@pytest.mark.parametrize("cancel", [False, True])
def test_later_failure_restores_route_and_security(tmp_path, mode, cancel):
    first, second, third = _targets(tmp_path)
    entry = tmp_path / "active"
    if mode != "create":
        junction.set_junction(entry, first, tmp_path)
    previous = junction._state(entry, tmp_path)
    with pytest.raises(KeyboardInterrupt if cancel else RuntimeError):
        with junction.junction_transaction(entry, tmp_path) as change:
            if mode == "remove":
                junction.remove_junction(entry, tmp_path)
                change.checkpoint(removed=True)
            else:
                junction.set_junction(entry, second, tmp_path)
                change.checkpoint(second)
            change.verify()
            raise KeyboardInterrupt() if cancel else RuntimeError("dependent PATH failure")
    if mode == "create":
        assert not os.path.lexists(entry)
    else:
        actual = junction._state(entry, tmp_path)
        assert (actual.data, actual.security, actual.protected) == (previous.data, previous.security, previous.protected)
        assert entry.resolve() == first
        if mode == "switch":
            assert actual.identity == previous.identity
    assert all((directory / "marker").read_text() == "preserved" for directory in (first, second, third))
    assert not list(tmp_path.glob(".opskit-junction-restore-*"))


def test_guard_rejects_competing_write_and_releases_after_commit(tmp_path):
    first, second, third = _targets(tmp_path)
    entry = tmp_path / "active"
    junction.set_junction(entry, first, tmp_path)
    with junction.junction_transaction(entry, tmp_path) as change:
        junction.set_junction(entry, second, tmp_path)
        change.checkpoint(second)
        with pytest.raises(OSError):
            junction.set_junction(entry, third, tmp_path)
        change.verify()
    assert entry.resolve() == second
    junction.set_junction(entry, third, tmp_path)
    assert entry.resolve() == third


def test_guard_releases_on_exception(tmp_path):
    first, second, third = _targets(tmp_path)
    entry = tmp_path / "active"
    junction.set_junction(entry, first, tmp_path)
    with pytest.raises(RuntimeError):
        with junction.junction_transaction(entry, tmp_path) as change:
            junction.set_junction(entry, second, tmp_path)
            change.checkpoint(second)
            raise RuntimeError("cancel")
    junction.set_junction(entry, third, tmp_path)
    assert entry.resolve() == third


def test_checkpoint_rejects_different_external_target(tmp_path):
    first, second, third = _targets(tmp_path)
    entry = tmp_path / "active"
    junction.set_junction(entry, first, tmp_path)
    with pytest.raises(InstallError, match="rollback incomplete"):
        with junction.junction_transaction(entry, tmp_path) as change:
            junction.set_junction(entry, second, tmp_path)
            junction.set_junction(entry, third, tmp_path)
            change.checkpoint(second)
    assert entry.resolve() == third


def test_checkpoint_rejects_replaced_entry_even_with_matching_target(tmp_path):
    first, second, _ = _targets(tmp_path)
    entry = tmp_path / "active"
    junction.set_junction(entry, first, tmp_path)
    original = junction._state(entry, tmp_path).identity
    with pytest.raises(InstallError, match="rollback incomplete"):
        with junction.junction_transaction(entry, tmp_path) as change:
            junction.remove_junction(entry, tmp_path)
            junction.set_junction(entry, second, tmp_path)
            assert junction._state(entry, tmp_path).identity != original
            change.checkpoint(second)
    assert entry.resolve() == second


def test_existing_non_junction_refused_before_callback(tmp_path):
    entry = tmp_path / "active"
    entry.mkdir()
    (entry / "user-data").write_text("preserved")
    with pytest.raises(InstallError, match="unmanaged junction"):
        with junction.junction_transaction(entry, tmp_path):
            pytest.fail("unmanaged directory reached callback")
    assert (entry / "user-data").read_text() == "preserved"


def test_guarded_checkpoint_does_not_use_path_only_identity(tmp_path, monkeypatch):
    first, _, _ = _targets(tmp_path)
    entry = tmp_path / "active"
    junction.set_junction(entry, first, tmp_path)
    actual = junction._identity
    monkeypatch.setattr(junction, "_identity", lambda handle: (actual(handle)[0], actual(handle)[1] + 1))
    with pytest.raises(InstallError, match="identity changed"):
        with junction.junction_transaction(entry, tmp_path):
            pytest.fail("unbound reparse data accepted")


def test_acl_restoration_failure_is_explicit_and_keeps_target_data(tmp_path, monkeypatch):
    first, _, _ = _targets(tmp_path)
    entry = tmp_path / "active"
    junction.set_junction(entry, first, tmp_path)
    def denied(*args):
        raise PermissionError("ACL restoration denied")
    monkeypatch.setattr(junction, "_restore_security", denied)
    with pytest.raises(InstallError, match="rollback incomplete") as failed:
        with junction.junction_transaction(entry, tmp_path) as change:
            junction.remove_junction(entry, tmp_path)
            change.checkpoint(removed=True)
            raise RuntimeError("PATH removal failed")
    assert isinstance(failed.value.__cause__, PermissionError)
    assert str(failed.value.__cause__) == "ACL restoration denied"
    assert isinstance(failed.value.__cause__.__context__, RuntimeError)
    assert str(failed.value.__cause__.__context__) == "PATH removal failed"
    assert (first / "marker").read_text() == "preserved"
    assert not list(tmp_path.glob(".opskit-junction-restore-*"))


def test_linked_parent_rejected_without_path_is_junction_method(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    junction.set_junction(linked, real, tmp_path)
    monkeypatch.delattr(Path, "is_junction", raising=False)
    with pytest.raises(InstallError, match="parent must not be a link"):
        with junction.junction_transaction(linked / "active", linked):
            pytest.fail("junction parent reached callback")
    assert linked.resolve() == real
