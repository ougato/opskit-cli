"""Lifecycle publication and rollback tests independent of the native launcher."""
import pytest

from core.native_acquisition import Acquisition
from core.plugin import load_manifest
from core.plugin_install_receipt import content_receipt
from core.plugin_trust import compute_fingerprint, grant, trusted_record
from plugin import commands


def write_plugin(root, version):
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        f"name: demo\nversion: {version}\napi_version: 3\nkind: python\nentry: demo\n")
    (root / "demo.py").write_text("raise RuntimeError('MUST_NOT_IMPORT')\n")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    root = tmp_path / "plugins/demo"
    write_plugin(root, "1.0.0")
    source = str(tmp_path / "source")
    receipt = content_receipt(root)
    grant("demo", compute_fingerprint(root), "1.0.0", source, "main",
          installation_receipt=receipt, source_commit="a" * 40)
    def populate(origin, destination, **kwargs):
        assert origin == source and kwargs["branch"] == "main"
        write_plugin(destination, "1.1.0")
        return Acquisition(source, "b" * 40, compute_fingerprint(destination), content_receipt(destination))
    monkeypatch.setattr("core.native_acquisition.populate", populate)
    return root, receipt


def test_update_replaces_tree_and_revokes_before_publication(installed, monkeypatch):
    root, _ = installed
    publish = commands.publish_new_directory
    def observe(source, destination):
        assert trusted_record("demo")["fingerprint"] == ""
        publish(source, destination)
    monkeypatch.setattr(commands, "publish_new_directory", observe)
    assert commands._update_locked(load_manifest(root)) == (True, "updated")
    assert load_manifest(root).version == "1.1.0"
    record = trusted_record("demo")
    assert record["fingerprint"] == "" and record["source_commit"] == "b" * 40
    assert record["installation_receipt"] == content_receipt(root)
    assert not list(root.parent.glob(".opskit-update-*"))


def test_update_publication_failure_restores_old_bytes_without_regrant(installed, monkeypatch):
    root, receipt = installed
    publish = commands.publish_new_directory
    def fail(source, destination):
        if source.name == "payload":
            raise OSError("injected publication failure")
        publish(source, destination)
    monkeypatch.setattr(commands, "publish_new_directory", fail)
    assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt
    assert trusted_record("demo")["fingerprint"] == ""
    assert not list(root.parent.glob(".opskit-update-*"))


def test_update_does_not_overwrite_late_independent_writer(installed, monkeypatch):
    root, receipt = installed
    publish = commands.publish_new_directory
    def compete(source, destination):
        if source.name == "payload":
            root.mkdir()
            (root / "keep.txt").write_text("independent writer")
        publish(source, destination)
    monkeypatch.setattr(commands, "publish_new_directory", compete)
    assert not commands._update_locked(load_manifest(root))[0]
    assert (root / "keep.txt").read_text() == "independent writer"
    retained = list(root.parent.glob(".opskit-update-*"))
    assert len(retained) == 1
    assert content_receipt(retained[0] / "previous") == receipt


def test_local_modifications_are_not_blessed_by_reconfirming_trust(installed):
    root, original_receipt = installed
    (root / "notes.txt").write_text("user work")
    commands.grant_trust(load_manifest(root))
    assert trusted_record("demo")["installation_receipt"] == original_receipt
    assert not commands._update_locked(load_manifest(root))[0]
    assert (root / "notes.txt").read_text() == "user work"


def test_missing_legacy_receipt_is_not_permission_to_replace(installed):
    root, receipt = installed
    from core.plugin_trust import revoke
    revoke("demo")
    grant("demo", "", "1.0.0", "legacy-source")
    assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt


def test_changed_manifest_identity_is_rejected_without_touching_installed_tree(installed, monkeypatch):
    root, receipt = installed
    def populate(source, destination, **kwargs):
        write_plugin(destination, "1.1.0")
        file = destination / "plugin.yaml"
        file.write_text(file.read_text().replace("name: demo", "name: other"))
        return Acquisition(source, "b" * 40, compute_fingerprint(destination), content_receipt(destination))
    monkeypatch.setattr("core.native_acquisition.populate", populate)
    assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt
    assert not list(root.parent.glob(".opskit-update-*"))


def test_source_change_during_acquisition_is_not_overwritten(installed, monkeypatch):
    root, receipt = installed
    from core import native_acquisition
    populate = native_acquisition.populate
    def change_authority(source, destination, **kwargs):
        result = populate(source, destination, **kwargs)
        grant("demo", "f" * 64, "1.0.0", "new-operator-source", "other")
        return result
    monkeypatch.setattr(native_acquisition, "populate", change_authority)
    assert not commands._update_locked(load_manifest(root))[0]
    assert trusted_record("demo")["source"] == "new-operator-source"
    assert content_receipt(root) == receipt


@pytest.mark.parametrize("receipt,commit", [("bad", "a" * 40), ("a" * 64, "bad"), (None, "a" * 40)])
def test_invalid_installation_receipt_does_not_mutate_trust(installed, receipt, commit):
    before = trusted_record("demo")
    with pytest.raises(ValueError, match="invalid native installation receipt"):
        grant("demo", "", "9.0.0", "other-source", installation_receipt=receipt, source_commit=commit)
    assert trusted_record("demo") == before


@pytest.mark.parametrize("failure", [OSError, TimeoutError, KeyboardInterrupt])
def test_failed_or_cancelled_acquisition_keeps_original_content_and_authority(installed, monkeypatch, failure):
    root, receipt = installed
    before = trusted_record("demo")
    def partial(source, destination, **kwargs):
        write_plugin(destination, "1.1.0")
        raise failure()
    monkeypatch.setattr("core.native_acquisition.populate", partial)
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            commands._update_locked(load_manifest(root))
    else:
        assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt
    assert trusted_record("demo") == before
    assert not list(root.parent.glob(".opskit-update-*"))


def test_cancelled_publication_restores_original_without_regrant(installed, monkeypatch):
    root, receipt = installed
    publish = commands.publish_new_directory
    def cancel(source, destination):
        if source.name == "payload":
            raise KeyboardInterrupt
        publish(source, destination)
    monkeypatch.setattr(commands, "publish_new_directory", cancel)
    with pytest.raises(KeyboardInterrupt):
        commands._update_locked(load_manifest(root))
    assert content_receipt(root) == receipt
    assert trusted_record("demo")["fingerprint"] == ""
    assert not list(root.parent.glob(".opskit-update-*"))


def test_receipt_save_failure_rolls_back_new_tree(installed, monkeypatch):
    root, receipt = installed
    def deny(*args, **kwargs):
        raise OSError("receipt store unavailable")
    monkeypatch.setattr(commands, "grant", deny)
    assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt
    assert trusted_record("demo")["fingerprint"] == ""
    assert not list(root.parent.glob(".opskit-update-*"))
