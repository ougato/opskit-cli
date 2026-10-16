"""Retained workspaces consume admission slots without name-based deletion."""
import pytest

from core import plugin_workspace as workspace, plugin_workspace_budget as budget


def test_unprefixed_spill_is_not_ignored(tmp_path):
    for index in range(budget.MAX_WORKSPACES - 1):
        (tmp_path / f"opskit-sandbox-retained-{index}").mkdir()
    spill = tmp_path / "unconfirmed-evidence"
    spill.write_bytes(b"x")
    with pytest.raises(ValueError, match="byte quota"):
        budget.reserve_workspace(tmp_path)
    assert spill.read_bytes() == b"x"


def test_retained_unknown_workspace_names_exhaust_slots(tmp_path):
    for index in range(budget.MAX_WORKSPACES - 1):
        (tmp_path / f"opskit-sandbox-retained-{index}").mkdir()
    budget.reserve_workspace(tmp_path)
    last = tmp_path / "opskit-sandbox-unconfirmed"
    last.mkdir()
    (last / "evidence").write_bytes(b"keep")
    with pytest.raises(ValueError, match="slots exhausted"):
        budget.reserve_workspace(tmp_path)
    assert (last / "evidence").read_bytes() == b"keep"


def test_actual_worker_refuses_before_creation_then_accepts_after_release(tmp_path, monkeypatch):
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "runtime"))
    parent = tmp_path / "runtime/plugin-runtimes/workspaces"
    parent.mkdir(parents=True)
    retained = []
    for index in range(budget.MAX_WORKSPACES):
        path = parent / f"opskit-sandbox-old-{index}"
        path.mkdir()
        retained.append(path)
    with pytest.raises(RuntimeError, match="allocation unconfirmed"):
        workspace.allocate(timeout=30, request_text="{}")
    assert sorted(parent.glob("opskit-sandbox-*")) == sorted(retained)
    retained.pop().rmdir()
    lease = workspace.allocate(timeout=30, request_text="{}")
    assert (lease.root / "request.json").read_text() == "{}"
    assert lease.root.parent == parent.resolve()
    alternate = tmp_path / "alternate-temp"
    alternate.mkdir()
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(alternate))
    with pytest.raises(RuntimeError, match="allocation unconfirmed"):
        workspace.allocate(timeout=30, request_text="{}")
    assert not list(alternate.iterdir())
    workspace.cleanup(lease, timeout=30)
    assert all(path.is_dir() for path in retained)


def test_prefixed_file_and_unbounded_parent_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "opskit-sandbox-unknown"
    path.write_bytes(b"unowned")
    with pytest.raises(ValueError, match="unsafe retained"):
        budget.reserve_workspace(tmp_path)
    assert path.read_bytes() == b"unowned"
    path.unlink()
    monkeypatch.setattr(budget, "MAX_PARENT_ENTRIES", 2)
    for index in range(3):
        (tmp_path / str(index)).mkdir()
    with pytest.raises(ValueError, match="inventory exceeds"):
        budget.reserve_workspace(tmp_path)


def test_actual_bytes_and_full_growth_reservations(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "WORKSPACE_BYTES", 100)
    monkeypatch.setattr(budget, "MAX_WORKSPACE_BYTES", 400)
    root = tmp_path / "unknown-directory"
    root.mkdir()
    (root / "value").write_bytes(b"x" * 101)
    spill = tmp_path / "evidence"
    spill.write_bytes(b"x" * 199)
    result = budget.reserve_workspace(tmp_path)
    assert result.payload_bytes == result.charged_bytes == 300
    assert result.workspaces == 1
    spill.write_bytes(b"x" * 200)
    with pytest.raises(ValueError, match="byte quota"):
        budget.reserve_workspace(tmp_path)
    assert spill.stat().st_size == 200


def test_empty_workspaces_reserve_entries_before_growth(tmp_path, monkeypatch):
    (tmp_path / "unknown").mkdir()
    monkeypatch.setattr(budget, "MAX_WORKSPACE_ENTRIES", 2 * budget.WORKSPACE_ENTRIES - 1)
    with pytest.raises(ValueError, match="entry quota"):
        budget.reserve_workspace(tmp_path)


@pytest.mark.parametrize("kind", ["hardlink", "symlink"])
def test_unknown_linked_evidence_is_retained(tmp_path, kind):
    import os
    root = tmp_path / "unknown"
    root.mkdir()
    value = root / "value"
    value.write_bytes(b"keep")
    link = root / "other"
    try:
        if kind == "hardlink":
            os.link(value, link)
        else:
            link.symlink_to(value)
    except OSError:
        pytest.skip("link creation unavailable")
    with pytest.raises(ValueError, match="unsafe retained"):
        budget.reserve_workspace(tmp_path)
    assert value.read_bytes() == b"keep" and os.path.lexists(link)


def test_changed_file_fails_closed(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    value = tmp_path / "evidence"
    value.write_bytes(b"keep")
    original = Path.lstat
    reads = 0
    def changed(path, *args, **kwargs):
        nonlocal reads
        if path == value:
            reads += 1
            if reads == 2:
                before = original(path)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", changed)
    with pytest.raises(ValueError, match="file changed"):
        budget.reserve_workspace(tmp_path)
    assert value.read_bytes() == b"keep"
