"""Capacity checks and owned build cleanup protect user files and active tasks."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest

from core import plugin_disk_policy as disk
from core import plugin_build_history as history


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))


def test_policy_validation_and_actual_destination_space(tmp_path, monkeypatch):
    disk.configure(512, str(tmp_path), 3)
    assert disk.policy()["keep_builds"] == 3
    seen = []
    def usage(path):
        seen.append(path)
        return SimpleNamespace(free=600 * 1024**2)
    monkeypatch.setattr(shutil, "disk_usage", usage)
    assert disk.check_space(tmp_path / "future" / "build")["hard_quota"] is False
    with pytest.raises(OSError):
        disk.check_space(tmp_path, reserve_bytes=100 * 1024**2)
    assert seen == [tmp_path, tmp_path]
    for minimum, output, keep in [(True, "", 5), (255, "", 5), (512, "relative", 5), (512, "", 0)]:
        with pytest.raises(ValueError):
            disk.configure(minimum, output, keep)
    assert disk.policy()["minimum_free_mib"] == 512


def output(project, content=b"old"):
    directory = project / "build" / ("opskit-web-mobile-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    (directory / "index.html").write_bytes(content)
    history.record(project, {"directory": str(directory)})
    return directory


def test_cleanup_preserves_unknown_modified_and_retained_outputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    disk.configure(256, "", 1)
    old = output(project)
    modified = output(project)
    newest = output(project)
    unknown = project / "build" / ("opskit-web-mobile-" + uuid.uuid4().hex)
    unknown.mkdir()
    (unknown / "user.txt").write_text("preserve")
    before = history.inspect(project)
    old_row = next(row for row in before if row["directory"] == str(old))
    newest_row = next(row for row in before if row["directory"] == str(newest))
    (modified / "index.html").write_bytes(b"edited")
    changed = next(row for row in history.inspect(project) if row["directory"] == str(modified))
    assert not changed["valid"] and not changed["cleanup_candidate"]
    with pytest.raises(ValueError):
        history.clean(project, newest_row["id"], newest_row["record_sha256"])
    with pytest.raises(ValueError):
        history.clean(project, old_row["id"], "0" * 64)
    history.clean(project, old_row["id"], old_row["record_sha256"])
    assert not old.exists()
    assert modified.exists() and newest.exists() and (unknown / "user.txt").read_text() == "preserve"


def test_replaced_directory_is_not_a_cleanup_candidate(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    disk.configure(256, "", 1)
    old = output(project)
    output(project)
    old.rename(old.with_name(old.name + "-original"))
    old.mkdir()
    (old / "index.html").write_bytes(b"replacement")
    row = next(row for row in history.inspect(project) if row["directory"] == str(old))
    assert not row["valid"]
    with pytest.raises(ValueError):
        history.clean(project, row["id"], row["record_sha256"])
    assert (old / "index.html").read_bytes() == b"replacement"


def test_active_project_lock_blocks_cleanup(tmp_path):
    import subprocess
    import sys
    project = tmp_path / "project"
    project.mkdir()
    disk.configure(256, "", 1)
    output(project)
    output(project)
    row = history.inspect(project)[-1]
    code = ("from core.plugin_build_history import clean; clean(" + repr(str(project)) + ","
            + repr(row["id"]) + "," + repr(row["record_sha256"]) + ")")
    with history.project_lock(project):
        child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
    assert child.returncode != 0
    assert Path(row["directory"]).exists()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_low_space_rejects_creator_before_authorization(tmp_path, monkeypatch, language):
    from core import i18n
    from core.plugin_host_tools import creator_build
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    tool = tmp_path / "Creator.exe"
    tool.write_bytes(b"synthetic")
    with pytest.raises(OSError) as error:
        creator_build("demo", "a" * 64, str(tool), str(tmp_path), {"platform": "web-mobile"},
            authority=None, check_identity=lambda *a: True, deadline=100,
            checkpoint=lambda: None, output=lambda _: None)
    assert "plugin_native." not in str(error.value)
    assert ("空间不足" if language == "zh" else "Insufficient free space") in str(error.value)


def test_official_cli_policy_and_history(tmp_path):
    from typer.testing import CliRunner
    import main
    from core.cli import configure
    runner = CliRunner()
    try:
        result = runner.invoke(main.app, ["--output", "json", "plugin", "disk-policy",
            "--minimum-free-mib", "512", "--keep-builds", "2"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["hard_quota"] is False
        assert disk.policy()["keep_builds"] == 2
        result = runner.invoke(main.app, ["--output", "json", "plugin", "build-history", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"] == []
    finally:
        configure("text", False)


def test_custom_output_changes_scope_and_records_only_success(tmp_path, monkeypatch):
    import time
    from core import process
    from core.plugin_host_tools import creator_build
    project = tmp_path / "game"
    project.mkdir()
    destination = tmp_path / "outputs"
    destination.mkdir()
    tool = tmp_path / "Creator.exe"
    tool.write_bytes(b"synthetic")
    disk.configure(256, str(destination), 1)
    scopes = []
    class Authority:
        def require(self, scope, **kwargs):
            scopes.append(scope)
        def check(self, *args, **kwargs):
            pass
    def stream(command, **kwargs):
        assert ";buildPath=" + str(destination).replace("\\", "/") + ";" in command[4]
        target = destination / command[4].split("outputName=")[1]
        target.mkdir()
        (target / "index.html").write_text("new")
        yield "synthetic"
        return 36
    monkeypatch.setattr(process, "stream", stream)
    result = creator_build("demo", "a" * 64, str(tool), str(project), {"platform": "web-mobile"},
        authority=Authority(), check_identity=lambda *a: True, deadline=time.monotonic()+10,
        checkpoint=lambda: None, output=lambda _: None)
    assert result["success"]
    assert json.loads(scopes[0].resource)["output_directory"] == str(destination)
    assert history.inspect(project)[0]["directory"] == result["artifact"]["directory"]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_menu_can_restore_default_output(tmp_path, monkeypatch, language):
    from core import i18n
    from plugin import disk_menu
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    disk.configure(512, str(tmp_path), 3)
    answers = iter(["512", "", "3"])
    monkeypatch.setattr(disk_menu, "select", lambda *a, **k: "1")
    monkeypatch.setattr(disk_menu, "text_input", lambda *a, **k: next(answers))
    monkeypatch.setattr(disk_menu, "confirm", lambda *a, **k: True)
    messages = []
    monkeypatch.setattr(disk_menu, "print_info", messages.append)
    monkeypatch.setattr(disk_menu, "print_success", messages.append)
    monkeypatch.setattr(disk_menu, "pause", lambda: None)
    disk_menu.entry()
    assert disk.policy()["creator_output_directory"] == ""
    assert messages and all("plugin_native." not in message for message in messages)


def test_cleanup_rechecks_content_after_candidate_inspection(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    disk.configure(256, "", 1)
    old = output(project)
    output(project)
    row = next(row for row in history.inspect(project) if row["directory"] == str(old))
    original = history.inspect
    def changed_after_inspection(project):
        rows = original(project)
        (old / "index.html").write_bytes(b"changed between inspection and cleanup")
        return rows
    monkeypatch.setattr(history, "inspect", changed_after_inspection)
    with pytest.raises(ValueError, match="changed after inspection"):
        history.clean(project, row["id"], row["record_sha256"])
    assert (old / "index.html").read_bytes().startswith(b"changed")
