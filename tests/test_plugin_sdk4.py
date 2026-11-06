"""SDK 4 management compatibility and real worker/UI interface boundaries."""
from contextlib import closing
import inspect
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import typer

from core import plugin, plugin_inspection, plugin_sandbox, plugin_worker, prompt, sdk
from core.constants import PLUGIN_API_VERSION


def record(sdk_version=4, **extra):
    return {"name": "demo", "version": "1.0.0", "api_version": sdk_version,
            "kind": "python", "entry": "demo_pkg", **extra}


def write_plugin(root, directory, data):
    path = root / directory
    (path / "demo_pkg").mkdir(parents=True)
    (path / "demo_pkg" / "__init__.py").write_text("raise RuntimeError('must not import')", encoding="utf-8")
    (path / "plugin.yaml").write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("version", [True, False, "4", 4.0, 4.9, None, [], {}])
def test_sdk_version_requires_actual_integer(tmp_path, version):
    assert plugin._parse_manifest(record(version), tmp_path, allow_legacy=True) is None


def test_legacy_inventory_is_visible_but_not_executable_or_a_provider(tmp_path, monkeypatch):
    from core import plugin_services
    from plugin import commands
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "plugins"
    legacy = write_plugin(root, "old", record(3, provides=["storage"], uses=["other"]))
    current = write_plugin(root, "new", record(4, name="provider", provides=["storage"],
                           service_contracts={"storage": {"api_version": 1, "methods": ["read"]}}))
    assert plugin.load_manifest(legacy) is None
    with closing(plugin_inspection.ContentInspector()) as reader:
        all_items = reader.inventory(root, timeout=30)
        providers = reader.inventory(root, select="service", value="storage", timeout=30)
    old = next(item for item in all_items if item.name == "demo")
    assert old.api_version == 3 and old.uses == [{"service": "other", "source": ""}]
    assert [item.name for item in providers] == ["provider"]
    assert plugin_services._find_provider("storage").root == current
    assert plugin.load_plugin(old) is None
    with pytest.raises(plugin_sandbox.SandboxError):
        plugin_sandbox.check_authorization(old)
    with pytest.raises(ValueError):
        commands.grant_trust(old)
    assert legacy.is_dir()


def test_legacy_plugin_can_update_from_its_exact_receipted_source(tmp_path, monkeypatch):
    from core import native_acquisition, plugin_trust
    from core.plugin_install_receipt import content_receipt
    from plugin import commands
    root = tmp_path / "plugins"
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(root))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    old = write_plugin(root, "old", record(3))
    candidate = write_plugin(tmp_path, "candidate", record(4, version="1.1.0"))
    source = "https://example.invalid/demo.git"
    plugin_trust.grant("demo", plugin_trust.compute_fingerprint(old), "1.0.0", source, "main",
                       installation_receipt=content_receipt(old), source_commit="a" * 40)

    def acquire(actual_source, destination, **kwargs):
        assert actual_source == source and kwargs["branch"] == "main"
        shutil.copytree(candidate, destination)
        return SimpleNamespace(receipt=content_receipt(destination), source=source, commit="b" * 40)

    monkeypatch.setattr(native_acquisition, "populate", acquire)
    legacy = next(item for item in commands.manifests() if item.name == "demo")
    assert commands.update(legacy) == (True, "updated")
    upgraded = plugin.load_manifest(old)
    assert upgraded is not None and upgraded.api_version == 4 and upgraded.version == "1.1.0"
    assert plugin_trust.trusted_record("demo")["fingerprint"] == ""
    assert plugin_trust.trusted_record("demo")["source"] == source


def test_removing_legacy_plugin_keeps_its_data(tmp_path, monkeypatch):
    from plugin import commands
    root, data = tmp_path / "plugins", tmp_path / "data"
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(root))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
    old = write_plugin(root, "old", record(3))
    state = data / "plugin-data" / "demo" / "self"
    state.mkdir(parents=True)
    (state / "user.json").write_text("keep", encoding="utf-8")
    legacy = next(item for item in commands.manifests() if item.name == "demo")
    commands.remove(legacy)
    assert not old.exists()
    assert (state / "user.json").read_text(encoding="utf-8") == "keep"


def test_select_and_pagination_map_custom_keys_to_items(monkeypatch):
    seen = []
    answers = iter(["n", "x"])
    def select(**kwargs):
        seen.append(kwargs["choices"])
        return next(answers)
    monkeypatch.setattr(prompt, "select", select)
    chosen = prompt.paged_select(breadcrumb=["OpsKit"], items=list(range(10)),
                                 choice_of=lambda item, index: {"key": "x" if item == 9 else str(index + 1),
                                                                "label": str(item)},
                                 subtitle_of=lambda page, total: str(page), back_label="Back")
    assert chosen == 9
    assert any(choice["key"] == "n" for choice in seen[0])
    assert any(choice["key"] == "p" for choice in seen[1])


def test_numbered_select_automatically_pages_and_preserves_original_key(monkeypatch):
    from core import i18n, theme
    from rich.cells import cell_len
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang("zh")))
    theme.init()
    keys = iter(["n", "p", "n", "2"])
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: next(keys))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_render_header", lambda _: None)
    pages = []
    monkeypatch.setattr(prompt, "_render_options", lambda options, **_: pages.append(options))
    ready = []
    result = prompt.select(["OpsKit"], "选择", [{"key": str(i), "label": f"插件 {i}"}
                                                for i in range(1, 13)],
                           on_ready=lambda: ready.append(True), back_label="返回")
    assert result == "11" and ready == [True] and len(pages) == 4
    for page in pages:
        for key, label in page:
            if key in ("p", "n"):
                icon, text = label.split(" ", 1)
                assert cell_len(icon) == 2 and len(text) == 4


def test_plugin_manager_can_pick_a_plugin_after_the_first_page(monkeypatch):
    from core import i18n, theme
    from plugin import menu
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang("zh")))
    theme.init()
    manifests = [plugin.PluginManifest(name=f"plugin{i}", version="1.0.0", api_version=4,
                                       kind="python", entry="entry") for i in range(12)]
    monkeypatch.setattr(menu.commands, "manifests", lambda: manifests)
    keys = iter(["n", "2"])
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: next(keys))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_render_header", lambda _: None)
    monkeypatch.setattr(prompt, "_render_options", lambda *args, **kwargs: None)
    assert menu._pick("plugin.remove") is manifests[10]


@pytest.mark.parametrize("key", ["0", "p", "n", "10", ""])
def test_pagination_rejects_reserved_or_unreachable_keys(key):
    with pytest.raises(ValueError):
        prompt.paged_select(breadcrumb=[], items=[1], choice_of=lambda *_: {"key": key, "label": "x"},
                            subtitle_of=lambda *_: "", back_label="Back")


def test_noninteractive_multi_select_never_synthesizes_empty_user_choice(monkeypatch):
    from core.cli import non_interactive
    monkeypatch.setattr(prompt, "_auto_yes", True)
    token = non_interactive.set(True)
    try:
        assert prompt.multi_select([], "", []) == []
        with pytest.raises(typer.Exit):
            prompt.multi_select([], "", ["first"])
    finally:
        non_interactive.reset(token)


def test_worker_ui_preserves_sdk_signature_and_normalizes_cancellation(monkeypatch):
    from core import paths
    # _adapt_sdk executes in a separate worker in production. Restore all of its
    # bindings here so an in-process unit test cannot affect other tests.
    for name in sdk.__all__:
        monkeypatch.setattr(sdk, name, getattr(sdk, name))
    for name in ("select", "select_input", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"):
        monkeypatch.setattr(prompt, name, getattr(prompt, name))
    for name in ("plugin_data_dir", "plugins_dir"):
        monkeypatch.setattr(paths, name, getattr(paths, name))
    calls = []
    def broker(method, *args, **kwargs):
        calls.append((method, args, kwargs))
        if method == "select":
            return "1"
        if method == "select_input":
            return "0"
        if method == "multi_select":
            raise prompt.UserCancel()
        return []
    monkeypatch.setattr(plugin_worker, "broker", broker)
    plugin_worker._adapt_sdk("demo")
    assert sdk.select(["OpsKit"], "Select", [{"key": "1", "label": "One"}], "root", None, "Back", "Hint") == "1"
    assert calls[0][1] == () and calls[0][2]["description"] == "Hint"
    assert sdk.select_input([], "", []) is None
    assert sdk.multi_select([], "", ["x"]) is None
    with pytest.raises(TypeError):
        sdk.select([], "", [], on_ready=lambda: None)
    with pytest.raises(TypeError):
        sdk.open_service_menu("storage", [], source="ignored")
    assert "on_ready" not in inspect.signature(sdk.select).parameters
    assert sdk.host_asar_directories("root", "file", "dir") == []
    assert calls[-1][0] == "host.asar_directories"
    assert PLUGIN_API_VERSION == sdk.SDK_API_VERSION == 4


def test_developer_guide_covers_every_sdk_export():
    import re
    guide = (Path(__file__).resolve().parents[1] / "docs" / "plugin-development.md").read_text(encoding="utf-8")
    assert [name for name in sdk.__all__ if not re.search(r"\b" + re.escape(name) + r"\b", guide)] == []


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plugin_cli_help_resolves_nested_translations_before_boot(monkeypatch, language):
    from core import config, i18n
    from plugin.lifecycle_cli import register
    from typer.testing import CliRunner
    import main
    monkeypatch.setattr(config, "load_config", lambda: {"language": language})
    catalogue = main._preboot_locale()
    assert catalogue["plugin_native_lifecycle.list_help"] == i18n._load_lang(language)["plugin_native"]["lifecycle"]["list_help"]
    application = typer.Typer()
    register(application, translate=lambda key: catalogue["plugin_native_" + key])
    result = CliRunner().invoke(application, ["--help"])
    assert result.exit_code == 0, result.output
    assert "plugin_native." not in result.output
    assert catalogue["plugin_native_lifecycle.list_help"] in result.output


def test_requirements_match_declared_native_fetch_and_security_dependencies():
    tomllib = pytest.importorskip("tomllib")
    from packaging.requirements import Requirement
    root = Path(__file__).resolve().parents[1]
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    requirement_lines = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
    project = {req.name: req for req in map(Requirement, declared)}
    install = {req.name: req for req in map(Requirement, requirement_lines) if req.name}
    for name in ("dulwich", "anyio", "idna", "cryptography"):
        assert project[name].specifier == install[name].specifier
