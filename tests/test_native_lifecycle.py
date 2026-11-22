"""Lifecycle publication and rollback tests independent of the native launcher."""
import pytest

from core.native_acquisition import Acquisition
from core.native_sandbox import CapabilityRequest, GrantChoice
from core.plugin import load_manifest
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied
from core.plugin_install_receipt import content_receipt
from core.plugin_trust import compute_fingerprint, grant, trusted_record
from plugin import commands


def write_plugin(root, version):
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        f"name: demo\nversion: {version}\napi_version: 4\nkind: python\nentry: demo\n")
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


def test_update_replaces_tree_and_inherits_trust_after_publication(installed, monkeypatch):
    root, _ = installed
    approved = compute_fingerprint(root)
    publish = commands.publish_new_directory
    def observe(source, destination):
        assert trusted_record("demo")["fingerprint"] == approved
        publish(source, destination)
    monkeypatch.setattr(commands, "publish_new_directory", observe)
    assert commands._update_locked(load_manifest(root)) == (True, "updated")
    assert load_manifest(root).version == "1.1.0"
    record = trusted_record("demo")
    assert record["fingerprint"] == compute_fingerprint(root)
    assert record["fingerprint"] != approved and record["source_commit"] == "b" * 40
    assert record["installation_receipt"] == content_receipt(root)
    assert not list(root.parent.glob(".opskit-update-*"))


@pytest.mark.parametrize("prepare_ok", [True, False])
def test_automatic_update_prepares_published_bytes_before_reporting_success(installed, monkeypatch, prepare_ok):
    root, original_receipt = installed
    approved = compute_fingerprint(root)
    observed = []

    def prepare(manifest, receipt):
        assert manifest.root == root and manifest.version == "1.1.0"
        assert content_receipt(root) == receipt
        assert trusted_record("demo")["fingerprint"] == approved
        assert commands.trust_status(manifest) != commands.TRUST_OK
        observed.append(receipt)
        if not prepare_ok:
            raise ValueError("runtime preparation failed")

    monkeypatch.setattr("core.plugin_runtime_manager.prepare_update", prepare)
    phases = []
    ok, status = commands._update_locked(load_manifest(root), automatic=True,
                                         progress=lambda phase: phases.append(phase))
    assert phases == ["update_check", "update_fetch", "update_verify", "update_publish", "runtime"]
    assert ok is prepare_ok and len(observed) == 1
    if prepare_ok:
        assert status == "updated" and trusted_record("demo")["installation_receipt"] == observed[0]
    else:
        assert content_receipt(root) == original_receipt
        assert trusted_record("demo")["installation_receipt"] == original_receipt


def test_update_publication_failure_preserves_trust_of_restored_bytes(installed, monkeypatch):
    root, receipt = installed
    publish = commands.publish_new_directory
    def fail(source, destination):
        if source.name == "payload":
            raise OSError("injected publication failure")
        publish(source, destination)
    monkeypatch.setattr(commands, "publish_new_directory", fail)
    assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt
    assert commands.trust_status(load_manifest(root)) == commands.TRUST_OK
    assert not list(root.parent.glob(".opskit-update-*"))


def test_same_source_update_preserves_base_trust_when_permissions_change(installed, monkeypatch):
    root, _ = installed
    from core import native_acquisition
    populate = native_acquisition.populate

    def changed_permissions(source, destination, **kwargs):
        candidate = populate(source, destination, **kwargs)
        with (destination / "plugin.yaml").open("a") as manifest:
            manifest.write("permissions: [network]\n")
        return Acquisition(candidate.source, candidate.commit, compute_fingerprint(destination),
                           content_receipt(destination))

    monkeypatch.setattr(native_acquisition, "populate", changed_permissions)
    assert commands._update_locked(load_manifest(root)) == (True, "updated")
    assert commands.trust_status(load_manifest(root)) == commands.TRUST_OK


def test_explicit_revocation_is_not_restored_by_an_update(installed, monkeypatch):
    from core.plugin_trust import invalidate

    root, receipt = installed
    invalidate("demo")
    monkeypatch.setattr("core.native_acquisition.populate", lambda *a, **kw: pytest.fail("revoked plugin fetched"))
    ok, message = commands._update_locked(load_manifest(root))
    assert not ok and message == commands.t("plugin_native.errors.update_trust_required")
    assert content_receipt(root) == receipt
    assert trusted_record("demo")["fingerprint"] == ""


@pytest.mark.parametrize("role", ["caller", "provider"])
@pytest.mark.parametrize("new_version", [5, 6])
def test_update_inherits_service_grant_only_for_unchanged_contract(installed, monkeypatch, role, new_version):
    from core import native_acquisition
    from core.plugin_trust import grant_service, is_service_authorized

    root, _ = installed
    installation = trusted_record("demo")
    template = (
        "uses:\n  - service: storage\n    provider: peer\n    api_version: {version}\n    methods: [open_menu]\n"
        if role == "caller" else
        "provides: [storage]\nservice_contracts:\n  storage:\n    api_version: {version}\n    methods: [open_menu]\n"
    )

    def version_contract(directory, version):
        with (directory / "plugin.yaml").open("a", encoding="utf-8") as manifest:
            manifest.write(template.format(version=version))

    version_contract(root, 5)
    previous = compute_fingerprint(root)
    grant("demo", previous, installation["version"], installation["source"], installation["branch"],
          installation_receipt=content_receipt(root), source_commit=installation["source_commit"])
    peer_fingerprint = "c" * 64
    grant("peer", peer_fingerprint, "1.0.0")
    caller, provider = (("demo", "peer") if role == "caller" else ("peer", "demo"))
    old_fingerprints = {"demo": previous, "peer": peer_fingerprint}
    grant_service(caller, old_fingerprints[caller], "storage", provider, old_fingerprints[provider], 5, ["open_menu"])
    assert is_service_authorized(caller, old_fingerprints[caller], "storage", provider,
                                 old_fingerprints[provider], 5, "open_menu")
    populate = native_acquisition.populate

    def upgraded_contract(source, destination, **kwargs):
        candidate = populate(source, destination, **kwargs)
        version_contract(destination, new_version)
        return Acquisition(candidate.source, candidate.commit, compute_fingerprint(destination),
                           content_receipt(destination))

    monkeypatch.setattr(native_acquisition, "populate", upgraded_contract)
    assert commands._update_locked(load_manifest(root)) == (True, "updated")
    assert commands.trust_status(load_manifest(root)) == commands.TRUST_OK
    new_fingerprints = {**old_fingerprints, "demo": compute_fingerprint(root)}
    assert ("storage" in trusted_record(caller).get("service_grants", {})) == (new_version == 5)
    assert is_service_authorized(caller, new_fingerprints[caller], "storage", provider,
                                 new_fingerprints[provider], new_version, "open_menu") == (new_version == 5)
    assert not is_service_authorized(caller, old_fingerprints[caller], "storage", provider,
                                     old_fingerprints[provider], 5, "open_menu")


@pytest.mark.parametrize("fail", [False, True])
def test_update_inherits_permanent_directory_grants(installed, tmp_path, monkeypatch, fail):
    root, _ = installed
    old = compute_fingerprint(root)
    authority = CapabilityAuthority()
    read = CapabilityRequest("demo", old, "directory.read", str(tmp_path / "project"))
    write = CapabilityRequest("demo", old, "directory.read_write", str(tmp_path / "other"))
    manage = CapabilityRequest("demo", old, "directory.manage", str(tmp_path / "managed"))
    tool = CapabilityRequest("demo", old, "tool.execute", str(tmp_path / "tool.exe"))
    once = CapabilityRequest("demo", old, "directory.read", str(tmp_path / "once"))
    live = lambda name, fingerprint: trusted_record(name)["fingerprint"] == fingerprint
    for request, choice in ((read, GrantChoice.ALWAYS), (write, GrantChoice.ALWAYS),
                            (manage, GrantChoice.ALWAYS), (tool, GrantChoice.ALWAYS), (once, GrantChoice.ONCE)):
        authority.require(request, check_identity=live, prompt=lambda _, choice=choice: choice)
    if fail:
        def fail_grant(*args, **kwargs):
            raise OSError("injected trust store write failure")
        monkeypatch.setattr(commands, "grant", fail_grant)
    assert commands._update_locked(load_manifest(root))[0] is not fail
    current = compute_fingerprint(root)
    child = CapabilityRequest("demo", current, "directory.read", str(tmp_path / "project/build"))
    fresh = CapabilityAuthority()
    fresh.check(child, check_identity=live)
    if fail:
        assert current == old
        assert set(fresh.list_grants()) == {read, write, manage, tool}
    else:
        assert current != old
        assert set(fresh.list_grants()) == {tool, *(
            CapabilityRequest("demo", current, request.operation, request.resource) for request in (read, write, manage)
        )}
        for operation in ("directory.read", "directory.write", "directory.manage"):
            fresh.check(CapabilityRequest("demo", current, operation, str(tmp_path / "managed" / "child")),
                        check_identity=live)
        for request in (tool, once):
            with pytest.raises(CapabilityDenied):
                fresh.check(CapabilityRequest("demo", current, request.operation, request.resource),
                            check_identity=live)
        renewed = CapabilityRequest("demo", current, "directory.read", read.resource)
        fresh.revoke(renewed)
        with pytest.raises(CapabilityDenied) as error:
            fresh.check(child, check_identity=live)
        assert error.value.code == "missing_or_revoked"


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


def test_cancelled_publication_restores_original_with_existing_trust(installed, monkeypatch):
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
    assert commands.trust_status(load_manifest(root)) == commands.TRUST_OK
    assert not list(root.parent.glob(".opskit-update-*"))


def test_receipt_save_failure_rolls_back_new_tree(installed, monkeypatch):
    root, receipt = installed
    def deny(*args, **kwargs):
        raise OSError("receipt store unavailable")
    monkeypatch.setattr(commands, "grant", deny)
    assert not commands._update_locked(load_manifest(root))[0]
    assert content_receipt(root) == receipt
    assert commands.trust_status(load_manifest(root)) == commands.TRUST_OK
    assert not list(root.parent.glob(".opskit-update-*"))
