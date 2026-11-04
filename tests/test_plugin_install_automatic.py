"""One installation transaction prepares tools and runtime before publication."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import plugin_bootstrap as bootstrap
from core.native_sandbox import NativeSandboxError
from core.prompt import UserCancel
from plugin import commands, menu
from tests import test_plugin_install_publication as fixtures


@pytest.fixture
def installation(tmp_path, monkeypatch):
    return fixtures.installation.__wrapped__(tmp_path, monkeypatch)


def test_automatic_install_prepares_before_publication(installation, monkeypatch):
    plugins, origin = installation
    events = []
    monkeypatch.setattr(bootstrap, "ensure_fetch", lambda progress: events.append("tools"))
    def runtime(manifest, progress):
        assert not (plugins / "demo").exists()
        assert (manifest.root / "demo.py").exists()
        events.append("runtime")
    monkeypatch.setattr(bootstrap, "ensure_runtime", runtime)
    publish = commands.publish_new_directory
    def observe(source, dest):
        assert events == ["tools", "runtime"]
        events.append("publish")
        return publish(source, dest)
    monkeypatch.setattr(commands, "publish_new_directory", observe)
    manifest, error = commands.install(str(origin), alias="demo", automatic=True)
    assert not error and manifest and events == ["tools", "runtime", "publish"]
    assert commands.trust_status(manifest) != commands.TRUST_OK


@pytest.mark.parametrize("phase", ["tools", "runtime"])
def test_preparation_failure_never_publishes_or_grants(installation, monkeypatch, phase):
    plugins, origin = installation
    def tools(progress):
        if phase == "tools":
            raise OSError("synthetic failure")
    def runtime(manifest, progress):
        raise OSError("synthetic failure")
    monkeypatch.setattr(bootstrap, "ensure_fetch", tools)
    monkeypatch.setattr(bootstrap, "ensure_runtime", runtime)
    manifest, error = commands.install(str(origin), alias="demo", automatic=True)
    assert manifest is None and error
    assert not (plugins / "demo").exists()
    assert not list(plugins.glob(".opskit-install-*"))


def test_cancelled_acquisition_propagates_and_cleans(installation, monkeypatch):
    plugins, origin = installation
    monkeypatch.setattr(bootstrap, "ensure_fetch", lambda progress: None)
    def cancel(*args, **kwargs):
        raise UserCancel()
    monkeypatch.setattr("core.native_acquisition.populate", cancel)
    with pytest.raises(UserCancel):
        commands.install(str(origin), alias="demo", automatic=True)
    assert not (plugins / "demo").exists()
    assert not list(plugins.glob(".opskit-install-*"))


@pytest.mark.parametrize("cancelled", [False, True])
def test_declining_or_cancelling_trust_rolls_back(installation, monkeypatch, cancelled):
    plugins, origin = installation
    manifest, error = commands.install(str(origin), alias="demo")
    assert not error
    def review(*args, **kwargs):
        if cancelled:
            raise UserCancel()
        return False
    monkeypatch.setattr(menu, "confirm_trust", review)
    if cancelled:
        with pytest.raises(UserCancel):
            menu._confirm_install(manifest, str(origin))
    else:
        assert menu._confirm_install(manifest, str(origin)) is False
    assert not (plugins / "demo").exists()


def test_existing_tools_are_reused_without_download(monkeypatch):
    from core.native_fetch_catalog import FetchCatalog
    monkeypatch.setattr(FetchCatalog, "load", lambda self: (Path("runtime"), {}))
    monkeypatch.setattr(bootstrap, "_inputs", lambda *args: pytest.fail("unnecessary download"))
    bootstrap.ensure_fetch()


@pytest.mark.parametrize("url", ["http://www.python.org/file", "https://evil.example/file",
                                "https://user@www.python.org/file", "https://www.python.org:444/file"])
def test_bootstrap_rejects_unapproved_sources(url):
    with pytest.raises(ValueError):
        bootstrap._url(url)


def test_pinned_manifest_has_complete_sdk_wheels(monkeypatch):
    monkeypatch.setattr(bootstrap, "get_backend", lambda: SimpleNamespace(probe=lambda: None))
    assets = bootstrap._manifest()
    packages = {a["package"] for a in assets if a["kind"] == "wheel"}
    assert {"psutil", "rich", "pyyaml", "typer", "platformdirs"} <= packages
    for asset in assets:
        assert len(bytes.fromhex(asset["sha256"])) == 32
        assert "latest" not in asset["url"]


def test_bad_download_never_becomes_cache_file(tmp_path, monkeypatch):
    import hashlib
    import httpx
    asset = {"filename": "input.zip", "url": "https://www.python.org/input.zip",
             "size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
    response = httpx.Response(200, content=b"evil")
    transport = httpx.MockTransport(lambda request: response)
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=transport, **kw))
    import time
    with pytest.raises(ValueError, match="checksum"):
        bootstrap._download(asset, tmp_path, time.monotonic() + 20)
    assert not (tmp_path / "input.zip").exists()


@pytest.mark.parametrize("accepted", [True, False])
def test_menu_success_follows_runtime_and_trust(installation, monkeypatch, accepted):
    plugins, origin = installation
    ready, success = [], []
    monkeypatch.setattr(bootstrap, "ensure_fetch", lambda progress: None)
    monkeypatch.setattr(bootstrap, "ensure_runtime", lambda manifest, progress: ready.append(manifest.name))
    monkeypatch.setattr(menu, "text_input", lambda **kwargs: str(origin))
    monkeypatch.setattr(commands, "is_trusted_source", lambda source: True)
    monkeypatch.setattr(menu, "print_success", success.append)
    monkeypatch.setattr(menu, "print_error", lambda message: None)
    monkeypatch.setattr(menu, "print_info", lambda message: None)
    monkeypatch.setattr(menu, "pause", lambda: None)
    def review(manifest, **kwargs):
        assert ready == ["demo"]
        assert manifest.version == "1.0.0"
        assert (manifest.root / "plugin.yaml").exists()
        return accepted
    monkeypatch.setattr(menu, "confirm_trust", review)
    menu._install()
    assert len(success) == int(accepted)
    assert (plugins / "origin").exists() is accepted
