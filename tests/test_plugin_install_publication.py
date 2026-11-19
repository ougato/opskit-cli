"""Late independent writers must survive installation failure and publication."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from plugin import commands


@pytest.fixture
def installation(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(plugins))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "plugin.yaml").write_text(
        "name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo\n")
    (origin / "demo.py").write_text("# inert plugin; never imported\n")
    from tests.native_tool_fixtures import populate_synthetic
    monkeypatch.setattr("core.native_acquisition.populate", populate_synthetic)
    return plugins, origin


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("acquisition_failure", [False, True])
def test_real_other_writer_survives_acquisition_or_publication_failure(installation, monkeypatch, populated, acquisition_failure):
    plugins, origin = installation
    target = plugins / "demo"
    from core import native_acquisition
    real_populate = native_acquisition.populate

    def compete(source, destination, **kwargs):
        writer = "import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.mkdir();"
        if populated:
            writer += "(p/'keep').write_bytes(b'other writer')"
        subprocess.run([sys.executable, "-I", "-c", writer, str(target)], check=True, timeout=10)
        if acquisition_failure:
            from core.native_sandbox import NativeSandboxError
            raise NativeSandboxError("fetch_transfer_failed")
        return real_populate(source, destination, **kwargs)

    monkeypatch.setattr(native_acquisition, "populate", compete)
    manifest, error = commands.install(str(origin), alias="demo")
    assert manifest is None and error
    assert target.is_dir()
    assert sorted(item.name for item in target.iterdir()) == (["keep"] if populated else [])
    if populated:
        assert (target / "keep").read_bytes() == b"other writer"
    assert not list(plugins.glob(".opskit-install-*"))


def test_trust_write_failure_never_publishes(installation, monkeypatch, caplog):
    import logging

    plugins, origin = installation
    monkeypatch.setattr(logging.getLogger("opskit"), "propagate", True)

    def deny(*args, **kwargs):
        raise OSError("injected trust store failure")

    monkeypatch.setattr(commands, "grant", deny)
    manifest, error = commands.install(str(origin), alias="demo")
    from core.i18n import t
    assert manifest is None and error == t("plugin_native.errors.acquisition_failed")
    assert "trust store failure" not in error
    assert "phase=publication category=OSError location=plugin.commands:" in caplog.text
    assert "trust store failure" not in caplog.text
    assert not (plugins / "demo").exists()
    assert not list(plugins.glob(".opskit-install-*"))


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("reason,key", [(None, "workspace_failed"), ("capacity", "workspace_full"), ("busy", "workspace_busy")])
def test_workspace_failure_reports_safe_specific_diagnostic(installation, monkeypatch, language, reason, key):
    from core import i18n
    from core.plugin_sandbox import WorkspaceError
    plugins, origin = installation
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    def fail(*args, **kwargs):
        raise WorkspaceError("private filesystem detail", reason=reason)
    monkeypatch.setattr("core.native_acquisition.populate", fail)
    manifest, error = commands.install(str(origin), alias="demo")
    assert manifest is None and error == i18n.t("plugin_native.errors." + key)
    assert "private filesystem detail" not in error
    assert not (plugins / "demo").exists()
    assert not list(plugins.glob(".opskit-install-*"))


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("reason,key", [(None, "workspace_failed"), ("capacity", "workspace_full"), ("busy", "workspace_busy")])
def test_update_workspace_failure_preserves_installed_plugin_and_reports_category(
        installation, monkeypatch, language, reason, key):
    from core import i18n
    from core.plugin_install_receipt import content_receipt
    from core.plugin_sandbox import WorkspaceError
    from core.plugin_trust import trusted_record
    _, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert manifest is not None and not error
    commands.grant_trust(manifest)
    before, authority = content_receipt(manifest.root), trusted_record(manifest.name)
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))

    def fail(*args, **kwargs):
        raise WorkspaceError("private filesystem detail", reason=reason)

    monkeypatch.setattr("core.native_acquisition.populate", fail)
    ok, error = commands.update(manifest)
    assert not ok and error == i18n.t("plugin_native.errors." + key)
    assert "private filesystem detail" not in error
    assert content_receipt(manifest.root) == before
    assert trusted_record(manifest.name) == authority
    assert not list(manifest.root.parent.glob(".opskit-update-*"))


@pytest.mark.parametrize("language", ["zh", "en"])
def test_update_busy_is_localized_without_fetching(installation, monkeypatch, language):
    from contextlib import contextmanager
    from core import i18n
    from core.resource_lock import ResourceBusyError
    _, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert manifest is not None and not error
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))

    @contextmanager
    def busy(**kwargs):
        raise ResourceBusyError("private lock detail")
        yield

    monkeypatch.setattr("core.plugin_invocation_lock.hold", busy)
    monkeypatch.setattr("core.native_acquisition.populate", lambda *a, **k: pytest.fail("busy plugin fetched"))
    ok, error = commands.update(manifest)
    assert not ok and error == i18n.t("plugin_native.lifecycle.plugin_busy")


def test_cancel_cleans_private_candidate_and_preserves_target(installation, monkeypatch):
    plugins, origin = installation
    target = plugins / "demo"

    def cancel(source, destination, **kwargs):
        payload = Path(destination)
        payload.mkdir()
        (payload / "partial").write_bytes(b"partial")
        target.mkdir()
        (target / "keep").write_bytes(b"keep")
        raise KeyboardInterrupt

    monkeypatch.setattr("core.native_acquisition.populate", cancel)
    with pytest.raises(KeyboardInterrupt):
        commands.install(str(origin), alias="demo")
    assert (target / "keep").read_bytes() == b"keep"
    assert not list(plugins.glob(".opskit-install-*"))


def test_declined_install_removes_only_receipted_directory(installation):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    commands.rollback_install(manifest)
    assert not (plugins / "demo").exists()
    assert not list(plugins.glob(".opskit-rollback-*"))


@pytest.mark.parametrize("late", [False, True])
def test_declined_install_preserves_replacement_even_during_withdrawal(installation, monkeypatch, late):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    target = plugins / "demo"
    original = plugins / "original-retained"
    real_publish = commands.publish_new_directory

    def replace_target():
        target.rename(original)
        target.mkdir()
        (target / "keep").write_bytes(b"other writer")

    if late:
        def compete(source, destination, **kwargs):
            if source == target:
                replace_target()
            return real_publish(source, destination, **kwargs)
        monkeypatch.setattr(commands, "publish_new_directory", compete)
    else:
        replace_target()
    with pytest.raises(ValueError, match="identity changed"):
        commands.rollback_install(manifest)
    assert (target / "keep").read_bytes() == b"other writer"
    assert (original / "plugin.yaml").is_file()
    assert not list(plugins.glob(".opskit-rollback-*"))


def test_discovered_manifest_is_not_installation_receipt(installation):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    discovered = commands.load_manifest(plugins / "demo")
    with pytest.raises(ValueError, match="receipt required"):
        commands.rollback_install(discovered)
    assert (plugins / "demo" / "plugin.yaml").is_file()


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("failure", [PermissionError, TimeoutError])
def test_rollback_unreadable_content_preserves_installation(installation, monkeypatch, late, failure):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    target = plugins / "demo"
    original = (target / "demo.py").read_bytes()
    real_receipt = commands.content_receipt

    def unavailable(path):
        if (Path(path).name == "payload") is late:
            raise failure("injected content inspection failure")
        return real_receipt(path)

    monkeypatch.setattr(commands, "content_receipt", unavailable)
    with pytest.raises((ValueError, failure)):
        commands.rollback_install(manifest)
    assert (target / "demo.py").read_bytes() == original
    assert not list(plugins.glob(".opskit-rollback-*"))


def test_changed_withdrawn_content_is_retained_when_restore_target_occupied(installation, monkeypatch):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    target = plugins / "demo"
    real_publish = commands.publish_new_directory

    def compete(source, destination, **kwargs):
        if source == target:
            (source / "developer-work").write_bytes(b"keep withdrawn developer edit")
            result = real_publish(source, destination, **kwargs)
            target.mkdir()
            (target / "other-writer").write_bytes(b"keep occupying writer")
            return result
        return real_publish(source, destination, **kwargs)

    monkeypatch.setattr(commands, "publish_new_directory", compete)
    with pytest.raises(RuntimeError, match="retained at"):
        commands.rollback_install(manifest)
    assert (target / "other-writer").read_bytes() == b"keep occupying writer"
    withdrawn, = plugins.glob(".opskit-rollback-*/payload")
    assert (withdrawn / "developer-work").read_bytes() == b"keep withdrawn developer edit"
    assert (withdrawn / "plugin.yaml").is_file()


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("changed", ["code", "git", "cache", "empty_directory"])
def test_rollback_preserves_same_directory_developer_changes(installation, monkeypatch, late, changed):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    target = plugins / "demo"
    identity = target.stat().st_ino
    real_publish = commands.publish_new_directory
    relative = {"code": "demo.py", "git": ".git/config", "cache": ".pytest_cache/developer", "empty_directory": "empty"}[changed]

    def edit():
        path = target / relative
        if changed == "empty_directory":
            path.mkdir()
        else:
            path.parent.mkdir(exist_ok=True)
            # Even same-size edits must be compared by exact bytes.
            previous = path.read_bytes() if path.exists() else b"developer work"
            path.write_bytes(b"!" * len(previous))
        assert target.stat().st_ino == identity

    if late:
        def compete(source, destination, **kwargs):
            if source == target:
                edit()
            return real_publish(source, destination, **kwargs)
        monkeypatch.setattr(commands, "publish_new_directory", compete)
    else:
        edit()
    with pytest.raises(ValueError, match="changed"):
        commands.rollback_install(manifest)
    assert target.stat().st_ino == identity
    path = target / relative
    assert path.is_dir() if changed == "empty_directory" else path.read_bytes().startswith(b"!")
    assert not list(plugins.glob(".opskit-rollback-*"))


@pytest.mark.parametrize("different_alias", [False, True])
def test_real_installers_publish_only_one_identity(installation, tmp_path, different_alias):
    plugins, origin = installation
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    program = tmp_path / "installer.py"
    program.write_text('''import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from plugin import commands
def main():
    barrier = pathlib.Path(sys.argv[2])
    from core import native_acquisition
    from tests.native_tool_fixtures import populate_synthetic
    def simultaneous(source, destination, **kwargs):
        result = populate_synthetic(source, destination, **kwargs)
        (barrier / (sys.argv[3] + '.ready')).touch()
        deadline = time.monotonic() + 15
        while len(list(barrier.glob('*.ready'))) < 2:
            if time.monotonic() >= deadline:
                raise RuntimeError('second installer did not reach publication barrier')
            time.sleep(.01)
        return result
    native_acquisition.populate = simultaneous
    manifest, error = commands.install(sys.argv[5], alias=sys.argv[4])
    print(json.dumps({'installed': manifest is not None, 'error': error}), flush=True)
if __name__ == '__main__':
    main()
''')
    children = []
    reports = []
    try:
        for index, alias in enumerate(["demo", "other" if different_alias else "demo"]):
            children.append(subprocess.Popen([sys.executable, str(program), str(Path(commands.__file__).parent.parent),
                str(barrier), str(index), alias, str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            reports.append(json.loads(stdout))
    finally:
        from core.process import terminate_tree
        for child in children:
            if child.poll() is None:
                terminate_tree(child)
    assert sum(report["installed"] for report in reports) == 1, reports
    assert all(report["installed"] or report["error"] for report in reports)
    installed = [item for item in plugins.iterdir() if (item / "plugin.yaml").is_file()]
    assert len(installed) == 1
    assert not list(plugins.glob(".opskit-install-*"))


def test_declined_install_ui_reports_preserved_replacement(installation, monkeypatch):
    from plugin import menu
    from core import i18n

    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error and manifest is not None
    monkeypatch.setattr(commands, "install", lambda *a, **k: (manifest, ""))
    target = plugins / "demo"

    def decline(*args, **kwargs):
        target.rename(plugins / "retained-original")
        target.mkdir()
        (target / "keep").write_bytes(b"other writer")
        return False

    errors, pauses = [], []
    def message(key, **kwargs):
        return key + ": " + kwargs.get("error", "")
    monkeypatch.setattr(menu, "t", message)
    monkeypatch.setattr("core.installation_progress.t", message)
    monkeypatch.setattr(i18n, "t", message)
    monkeypatch.setattr(menu, "confirm_trust", decline)
    monkeypatch.setattr(menu, "text_input", lambda **kwargs: "fixture")
    monkeypatch.setattr(commands, "parse_install_input", lambda value: ("fixture", None, "demo"))
    monkeypatch.setattr(commands, "is_trusted_source", lambda value: True)
    monkeypatch.setattr(menu, "print_info", lambda *args: None)
    monkeypatch.setattr(menu, "print_error", errors.append)
    monkeypatch.setattr(menu, "pause", lambda: pauses.append(True))
    menu._install()
    assert len(errors) == 1 and "identity changed" in errors[0]
    assert pauses == [True]
    assert (target / "keep").read_bytes() == b"other writer"
