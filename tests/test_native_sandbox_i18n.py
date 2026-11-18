"""New native plugin UI must use the same bilingual catalogues as OpsKit."""

import pytest

from core import i18n
from core.native_sandbox import CapabilityRequest, GrantChoice, get_backend, NativeSandboxError
from core.plugin_capabilities import CapabilityDenied, prompt_grant


@pytest.fixture(autouse=True)
def isolated_cli_output():
    """Previous JSON CLI invocations must not redirect this UI verification."""
    from core import cli
    tokens = [(variable, variable.set(value)) for variable, value in (
        (cli.output_mode, "text"), (cli.non_interactive, False),
        (cli._result_stream, None),
    )]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_worker_language_does_not_read_or_write_host_config(monkeypatch, language):
    from core import config
    def forbidden(*args, **kwargs):
        pytest.fail("explicit worker language accessed host configuration")
    monkeypatch.setattr(config, "load_config", forbidden)
    monkeypatch.setattr(config, "set_config_value", forbidden)
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(i18n, "_lang", "en")
    i18n.init(language=language)
    assert i18n.current_lang() == language
    assert "plugin_native." not in i18n.t("plugin_native.errors.platform_unsupported")


def test_native_locale_key_parity_and_parameters():
    from string import Formatter
    zh = i18n._flatten(i18n._load_lang("zh")["plugin_native"])
    en = i18n._flatten(i18n._load_lang("en")["plugin_native"])
    assert set(zh) == set(en)
    for key in zh:
        assert zh[key] and en[key]
        fields = lambda value: {name for _, name, _, _ in Formatter().parse(value) if name}
        assert fields(zh[key]) == fields(en[key]), key


@pytest.mark.parametrize("language", ["zh", "en"])
def test_native_run_failure_uses_cli_error_boundary(monkeypatch, language):
    from typer.testing import CliRunner
    import main
    from plugin import cli
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    def refused(*args):
        raise NativeSandboxError("worker_failed")
    monkeypatch.setattr(cli, "run_plugin", refused)
    result = CliRunner().invoke(main.app, ["--non-interactive", "plugin", "run", "probe"])
    assert result.exit_code == 1
    assert i18n.t("plugin_native.errors.worker_failed") in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("language", ["zh", "en"])
def test_lifecycle_help_and_resolution_errors_follow_language(monkeypatch, language):
    monkeypatch.setenv("COLUMNS", "240")
    import typer
    from typer.testing import CliRunner
    from plugin import commands
    from plugin.lifecycle_cli import register
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(i18n, "_lang", language)
    monkeypatch.setattr(commands, "manifests", lambda: [])
    app = typer.Typer()
    register(app)
    runner = CliRunner()
    for command, key in (("inspect-state", "inspect_state_help"),
                         ("inspect-updates", "inspect_updates_help"),
                         ("recover-update", "recover_update_help"),
                         ("inspect-native-profiles", "inspect_profiles_help"),
                         ("recover-native-profile", "recover_profile_help"),
                         ("inspect-native-workspaces", "inspect_native_workspaces_help"),
                         ("recover-native-workspace", "recover_workspace_help"),
                         ("inspect-native-cache", "inspect_cache_help"),
                         ("clean-native-cache", "clean_cache_help"),
                         ("fingerprint", "fingerprint_help"),
                         ("authorize-service", "authorize_service_help")):
        result = runner.invoke(app, [command, "--help"], terminal_width=240)
        assert result.exit_code == 0, result.output
        expected = i18n.t("plugin_native.lifecycle." + key)
        assert " ".join(expected.split()) in " ".join(result.output.split())
    result = runner.invoke(app, ["show", "missing"])
    assert result.exit_code == 2
    assert i18n.t("plugin_native.lifecycle.not_found") in result.output
    result = runner.invoke(app, ["recover-update", "invalid", "--record-sha256", "BAD"])
    assert result.exit_code == 1
    assert i18n.t("plugin_native.lifecycle.update_recovery_failed") in result.output
    result = runner.invoke(app, ["recover-native-workspace", "invalid", "--record-sha256", "BAD"])
    assert result.exit_code == 1
    assert i18n.t("plugin_native.lifecycle.workspace_recovery_failed") in result.output
    result = runner.invoke(app, ["prepare", "--help"], terminal_width=240)
    assert result.exit_code == 0, result.output
    visible = "".join(result.output.replace("│", "").split())
    for key in ("plugin_path_help", "dependency_help", "python_runtime_help", "wheels_help", "lock_file_help"):
        assert "".join(i18n.t("plugin_native." + key).split()) in visible
    for command in ("install", "update"):
        result = runner.invoke(app, [command, "--help"], terminal_width=240)
        assert result.exit_code == 0
        assert "--fetch-profile" in result.output and "--allow-private" in result.output
        for key in ("fetch_profile_help", "allow_private_help"):
            expected = i18n.t("plugin_native." + key)
            # Rich wraps option descriptions inside table borders.
            visible = "".join(result.output.replace("│", "").split())
            assert "".join(expected.split()) in visible


@pytest.mark.parametrize("language,once,denied", [
    ("zh", "本次授权", "用户拒绝"), ("en", "Allow this time", "was denied"),
])
def test_authorization_prompt_and_errors_follow_language(monkeypatch, language, once, denied):
    from core import prompt
    from core.cli import non_interactive
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr("core.plugin_capabilities._interactive_input", lambda: True)
    captured = []
    def select(*args, **kwargs):
        captured.append((args, kwargs))
        return "1"
    monkeypatch.setattr(prompt, "select", select)
    token = non_interactive.set(False)
    try:
        value = CapabilityRequest("game_release", "a" * 64, "tool.execute", "creator.build")
        assert prompt_grant(value) is GrantChoice.ONCE
    finally:
        non_interactive.reset(token)
    from core.theme import get_icon
    assert captured[0][0][2][0]["label"] == f"{get_icon('success')} {once}"
    assert all("plugin_native." not in line for line in captured[0][1]["info_lines"])
    assert i18n.t("plugin_native.tool_warning") in captured[0][1]["info_lines"]
    assert captured[0][1]["back_label"] == f"{get_icon('back')} {i18n.t('plugin_native.back')}"
    assert captured[0][1]["back_label"]
    assert denied in str(CapabilityDenied("denied"))
    with pytest.raises(NativeSandboxError) as error:
        get_backend("linux")
    assert "linux" in str(error.value)
    assert "plugin_native." not in str(error.value)


@pytest.mark.parametrize("language,help_word", [("zh", "原生"), ("en", "native")])
def test_prepare_fetch_help_and_failure_are_localized(monkeypatch, capsys, language, help_word):
    import typer
    from typer.testing import CliRunner
    from core import cli
    from core.native_fetch_catalog import FetchCatalog
    from plugin.lifecycle_cli import register
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(i18n, "_lang", language)
    app = typer.Typer()
    register(app)
    runner = CliRunner()
    result = runner.invoke(app, ["prepare-fetch", "--help"])
    assert result.exit_code == 0
    assert help_word in result.output
    assert "--git-runtime" not in result.output and "--python-runtime" in result.output
    assert "--packages" not in result.output
    def fail(*args, **kwargs):
        raise ValueError("SECRET")
    monkeypatch.setattr(FetchCatalog, "prepare", fail)
    cli.configure("json", False)
    try:
        result = runner.invoke(app, ["prepare-fetch", "--python-runtime", "python"])
        assert result.exit_code == 2
        output = result.output + capsys.readouterr().out
        assert i18n.t("plugin_native.errors.fetch_prepare_invalid") in output
        assert "SECRET" not in output and "plugin_native." not in output
    finally:
        cli.configure("text", False)
