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
    monkeypatch.setattr("core.plugin_disk_policy.check_space", lambda *args, **kwargs: None)
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


@pytest.mark.parametrize("python_local,git_local,bad_git,expected", [
    (True, True, False, []), (False, True, False, ["python"]),
    (True, False, False, []), (False, False, False, ["python"]),
    (True, True, True, []),
    ("broken", True, False, ["python"]),
])
def test_only_missing_or_failed_local_components_are_downloaded(tmp_path, monkeypatch, python_local, git_local, bad_git, expected):
    from contextlib import contextmanager
    import zipfile
    from core import local_toolchain, native_preparation, native_interpreter, native_fetch_catalog
    events, downloads = [], []
    class Catalog:
        prepared = False
        def load(self):
            if not self.prepared:
                raise FileNotFoundError()
            return None
        def prepare(self, python, git=None):
            if python_local == "broken" and Path(python).name == "python-0":
                raise NativeSandboxError("worker_failed")
            self.prepared = True
    monkeypatch.setattr(native_fetch_catalog, "FetchCatalog", Catalog)
    @contextmanager
    def workspace():
        yield tmp_path
    monkeypatch.setattr(native_preparation, "workspace", workspace)
    monkeypatch.setattr(local_toolchain, "python_candidates", lambda: [Path("python")] if python_local else [])
    monkeypatch.setattr(local_toolchain, "git_candidates", lambda: pytest.fail("Git must not be prepared"))
    monkeypatch.setattr(local_toolchain, "snapshot", lambda source, target, kind: target)
    def probe(source, **kwargs):
        if python_local == "broken" and source.name == "python-0":
            raise NativeSandboxError("worker_failed")
        return {SimpleNamespaceTag()}, {"python_version": "3.14"}
    monkeypatch.setattr(native_interpreter, "probe", probe)
    @contextmanager
    def inputs(kinds, progress):
        kind = next(iter(kinds))
        downloads.append(kind)
        filename = tmp_path / (kind + ".zip")
        with zipfile.ZipFile(filename, "w") as archive:
            archive.writestr("python.exe", b"fixture")
        yield tmp_path, [({"kind": kind}, filename)]
    monkeypatch.setattr(bootstrap, "_inputs", inputs)
    bootstrap.ensure_fetch(lambda phase, name="": events.append(phase))
    assert downloads == expected
    assert events == (["tools", "repair"] if expected else ["tools"])


class SimpleNamespaceTag:
    platform = "win_amd64"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_install_progress_does_not_print_package_names(monkeypatch, language):
    from core import i18n
    from core import installation_progress as ui
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    seen = []
    class Status:
        def __init__(self, message, **kwargs):
            assert kwargs["spinner"] == "dots"
            assert kwargs["spinner_style"] == ui.get_color("success")
            assert message.style == ui.get_color("success")
            seen.append(str(message))
        def start(self):
            pass
        def stop(self):
            pass
        def update(self, message):
            seen.append(str(message))
    monkeypatch.setattr(ui, "Status", Status)
    monkeypatch.setattr(ui, "console", SimpleNamespace(is_terminal=True, print=lambda: None))
    with ui.InstallationProgress() as progress:
        progress("download", "python-very-long-archive-name.zip")
        assert seen == []
        for phase in ("tools", "repair", "runtime"):
            progress(phase, "internal-name")
    assert len(seen) == 3
    assert not any("internal-name" in text or "plugin_native." in text for text in seen)
