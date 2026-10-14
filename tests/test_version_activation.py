"""Activation must compensate failed routing and check the executed binary."""
import copy
import os
import sys

import pytest

from software._shared.activation import ActivationRollbackError, activate_version, verify_command_version
from software.base import InstallError


@pytest.mark.parametrize("stage", ["save", "apply", "verify", "cancel"])
def test_failed_activation_restores_routing_link_and_path(monkeypatch, stage):
    old = {"active_version": "1.0", "versions": ["1.0", "2.0"]}
    state = {"snapshot": copy.deepcopy(old), "link": "1.0"}
    monkeypatch.setenv("PATH", "original-path")

    def save(data):
        state["snapshot"] = copy.deepcopy(data)
        if stage == "save" and data["active_version"] == "2.0":
            raise OSError("partial write")

    def apply():
        state["link"] = "2.0"
        os.environ["PATH"] = "changed-path"
        if stage == "apply":
            raise OSError("partial link")

    def verify():
        if stage == "cancel":
            raise KeyboardInterrupt()
        raise InstallError("wrong binary")

    with pytest.raises(KeyboardInterrupt if stage == "cancel" else (OSError, InstallError)):
        activate_version(old, {"active_version": "2.0"}, save=save, apply=apply,
                         restore=lambda: state.update(link="1.0"), verify=verify)
    assert state == {"snapshot": old, "link": "1.0"}
    assert old["active_version"] == "1.0"
    assert os.environ["PATH"] == "original-path"


def test_restore_failure_is_explicit_and_other_restore_still_runs():
    saved = []

    def fail():
        raise OSError("failed")

    with pytest.raises(ActivationRollbackError, match="rollback incomplete"):
        activate_version({"active_version": "1"}, {"active_version": "2"},
                         save=lambda data: saved.append(data), apply=fail,
                         restore=fail, verify=lambda: None)
    assert saved[-1]["active_version"] == "1"


def test_version_check_executes_real_supervised_python(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: sys.executable)
    verify_command_version("python", ".".join(map(str, sys.version_info[:3])))
    with pytest.raises(InstallError, match="does not match"):
        verify_command_version("python", "0.0.0")


def test_version_prefix_cannot_match_longer_version(monkeypatch):
    import core.process
    monkeypatch.setattr("shutil.which", lambda _: "test-binary")

    def output(*args, **kwargs):
        yield "product 1.20.0"
        return 0

    monkeypatch.setattr(core.process, "stream", output)
    with pytest.raises(InstallError, match="does not match"):
        verify_command_version("product", "1.2")


def test_corrupt_snapshot_is_not_treated_as_empty(tmp_path, monkeypatch):
    from software._shared.snapshot import SnapshotStore
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    store = SnapshotStore("snapshots", "python.json")
    store.path.parent.mkdir()
    store.path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid software snapshot"):
        store.load()
    assert store.path.read_text(encoding="utf-8") == "[]"
