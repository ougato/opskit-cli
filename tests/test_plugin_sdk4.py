"""SDK 4 manifest, worker and UI interface boundaries."""
from contextlib import closing
import inspect
import json
from pathlib import Path

import pytest
import typer

from core import plugin, plugin_inspection, plugin_worker, prompt, sdk
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
    assert plugin._parse_manifest(record(version), tmp_path) is None


@pytest.mark.parametrize("permissions", [None, "network", {}, 0, ["network", "network"], [None], [[]]])
def test_invalid_permissions_are_rejected(tmp_path, permissions):
    assert plugin._parse_manifest(record(permissions=permissions), tmp_path) is None


@pytest.mark.parametrize("field", ["uses", "provides"])
@pytest.mark.parametrize("value", [None, "", False, 0, {}])
def test_invalid_service_declaration_containers_are_rejected(tmp_path, field, value):
    assert plugin._parse_manifest(record(**{field: value}), tmp_path) is None


@pytest.mark.parametrize("field", ["label", "description", "group_label"])
@pytest.mark.parametrize("value", [None, "", [], False])
def test_invalid_localized_metadata_containers_are_rejected(tmp_path, field, value):
    assert plugin._parse_manifest(record(**{field: value}), tmp_path) is None


@pytest.mark.parametrize("extra", [
    {"label": {"zh": "名称\n伪造提示"}}, {"group_label": {"en": "Name\x1b[31m"}},
    {"icon": "\x1b[2J"}, {"group_icon": ["icon"]}, {"label": {"en": "x" * 257}},
])
def test_menu_metadata_rejects_terminal_controls_and_invalid_types(tmp_path, extra):
    assert plugin._parse_manifest(record(**extra), tmp_path) is None


def test_old_sdk_is_not_discovered_and_its_files_are_preserved(tmp_path, monkeypatch):
    from core import plugin_services
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
    assert [item.name for item in all_items] == ["provider"]
    assert [item.name for item in providers] == ["provider"]
    assert plugin_services._find_provider("storage").root == current
    assert legacy.is_dir()


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


def test_worker_state_namespace_requires_exact_plugin_identity(monkeypatch, tmp_path):
    from core import paths
    for name in sdk.__all__:
        monkeypatch.setattr(sdk, name, getattr(sdk, name))
    for name in ("select", "select_input", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"):
        monkeypatch.setattr(prompt, name, getattr(prompt, name))
    for name in ("plugin_data_dir", "plugins_dir"):
        monkeypatch.setattr(paths, name, getattr(paths, name))
    monkeypatch.setattr(plugin_worker, "_data_root", lambda: tmp_path)
    plugin_worker._adapt_sdk("my_plugin")
    assert sdk.plugin_data_dir("my_plugin") == tmp_path / "state"
    with pytest.raises(PermissionError):
        sdk.plugin_data_dir("my-plugin")


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
