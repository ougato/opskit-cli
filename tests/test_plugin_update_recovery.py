"""Interrupted update rollback preserves independent writers and all evidence."""
import subprocess
import sys

import pytest

from core import plugin_update_recovery as recovery
from core.plugin_install_receipt import content_receipt
from core.plugin_state import _directory
from core.plugin_trust import grant, trusted_record


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    parent = tmp_path / "plugins"
    parent.mkdir()
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(parent))
    root = parent / "demo"
    root.mkdir()
    (root / "old.txt").write_text("original")
    work = parent / (".opskit-update-" + "a" * 32)
    work.mkdir()
    candidate = work / "payload"
    candidate.mkdir()
    (candidate / "new.txt").write_text("new version")
    recovery.record(work, plugin="demo", root=root, old=_directory(root), candidate=_directory(candidate),
                    old_receipt=content_receipt(root), candidate_receipt=content_receipt(candidate))
    grant("demo", "b" * 64, "1.0.0")
    digest = recovery.inspect()[0]["record_sha256"]
    return root, work, digest


@pytest.mark.parametrize("stage", ["prepared", "withdrawn", "published"])
def test_recovery_restores_old_bytes_without_regrant_and_is_repeatable(interrupted, stage):
    root, work, digest = interrupted
    if stage != "prepared":
        root.rename(work / "previous")
    if stage == "published":
        (work / "payload").rename(root)
    result = recovery.recover("a" * 32, digest)
    assert result == {"transaction": "a" * 32, "restored": True, "evidence_retained": True}
    assert (root / "old.txt").read_text() == "original"
    assert not trusted_record("demo").get("fingerprint")
    assert (work / recovery.RECORD).is_file()
    assert recovery.recover("a" * 32, digest) == result
    retained = work / ("recovered-candidate" if stage == "published" else "payload")
    assert (retained / "new.txt").read_text() == "new version"


@pytest.mark.parametrize("change", ["digest", "backup", "independent", "journal", "workspace"])
def test_changed_resources_are_never_overwritten(interrupted, change):
    root, work, digest = interrupted
    root.rename(work / "previous")
    if change == "digest":
        digest = "0" * 64
    elif change == "backup":
        (work / "previous/old.txt").write_text("developer edit")
    elif change == "independent":
        root.mkdir()
        (root / "keep.txt").write_text("independent writer")
    elif change == "journal":
        (work / recovery.RECORD).write_text("{}")
    elif change == "workspace":
        saved = work.with_name(work.name + "-saved")
        work.rename(saved)
        work.mkdir()
        (work / recovery.RECORD).write_bytes((saved / recovery.RECORD).read_bytes())
    with pytest.raises((ValueError, OSError)):
        recovery.recover("a" * 32, digest)
    if change == "independent":
        assert (root / "keep.txt").read_text() == "independent writer"
    if change == "backup":
        assert (work / "previous/old.txt").read_text() == "developer edit"


def test_recovery_does_not_delete_unknown_or_invalid_workspaces(interrupted):
    root, work, digest = interrupted
    unknown = work.parent / ".opskit-update-unknown"
    unknown.mkdir()
    (unknown / "keep").write_text("unknown")
    values = recovery.inspect()
    assert any(item == {"transaction": "unknown", "valid": False} for item in values)
    assert (unknown / "keep").read_text() == "unknown"


@pytest.mark.parametrize("stage", ["withdrawn", "published", "authority"])
def test_real_update_process_exit_retains_recoverable_record(tmp_path, monkeypatch, stage):
    parent = tmp_path / "plugins"
    parent.mkdir()
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(parent))
    origin = tmp_path / "origin"
    origin.mkdir()
    root = parent / "demo"
    root.mkdir()
    manifest = "name: demo\nversion: {version}\napi_version: 3\nkind: python\nentry: demo\n"
    for path, version in ((root, "1.0.0"), (origin, "2.0.0")):
        (path / "plugin.yaml").write_text(manifest.format(version=version))
        (path / "demo.py").write_text("# " + version)
    before = content_receipt(root)
    grant("demo", "b" * 64, "1.0.0", str(origin), installation_receipt=before, source_commit="a" * 40)
    program = '''
import os,sys
from pathlib import Path
from plugin import commands
from core.plugin import load_manifest
from core import native_acquisition
from tests.native_tool_fixtures import populate_synthetic
native_acquisition.populate=populate_synthetic
real=commands.publish_new_directory
def publish(source,destination):
    real(source,destination)
    if (sys.argv[2]=='withdrawn' and Path(destination).name=='previous') or (sys.argv[2]=='published' and Path(destination)==Path(sys.argv[1])):
        os._exit(73)
commands.publish_new_directory=publish
grant=commands.grant
def publish_authority(*args,**kwargs):
    grant(*args,**kwargs)
    if sys.argv[2]=='authority': os._exit(73)
commands.grant=publish_authority
commands.update(load_manifest(Path(sys.argv[1])))
'''
    child = subprocess.run([sys.executable, "-c", program, str(root), stage],
                           capture_output=True, text=True, timeout=45)
    assert child.returncode == 73, child.stdout + child.stderr
    values = recovery.inspect()
    assert len(values) == 1 and values[0]["valid"] is True
    recovery.recover(values[0]["transaction"], values[0]["record_sha256"])
    assert content_receipt(root) == before
    assert "1.0.0" in (root / "plugin.yaml").read_text()
    authority = trusted_record("demo")
    assert authority["installation_receipt"] == before
    assert authority["version"] == "1.0.0"
    assert authority["fingerprint"] == ""


def test_clean_inspection_does_not_create_plugins_directory(tmp_path, monkeypatch):
    parent = tmp_path / "not-created/plugins"
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(parent))
    assert recovery.inspect() == []
    assert not parent.parent.exists()


def test_record_changed_during_content_validation_prevents_directory_moves(interrupted, monkeypatch):
    root, work, digest = interrupted
    root.rename(work / "previous")
    receipt = recovery.content_receipt
    def change(path):
        result = receipt(path)
        if path == work / "previous":
            with (work / recovery.RECORD).open("a") as stream:
                stream.write(" ")
        return result
    monkeypatch.setattr(recovery, "content_receipt", change)
    with pytest.raises(ValueError, match="record changed"):
        recovery.recover("a" * 32, digest)
    assert (work / "previous/old.txt").read_text() == "original"
    assert not root.exists()
