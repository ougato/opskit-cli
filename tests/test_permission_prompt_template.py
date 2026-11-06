"""The host-owned consent template and public menu information rows."""
from io import StringIO

import pytest
from rich.console import Console

from core import i18n, plugin_capabilities, prompt, theme
from core.native_sandbox import CapabilityRequest, GrantChoice


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("key,expected", [("1", GrantChoice.ONCE), ("2", GrantChoice.ALWAYS),
                                          ("0", None), ("\x1b", None)])
def test_directory_consent_renders_each_field_and_handles_choices(monkeypatch, language, key, expected):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", Console(file=output, width=240, force_terminal=False))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: key)
    monkeypatch.setattr(plugin_capabilities, "_interactive_input", lambda: True)
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    theme.init()
    request = CapabilityRequest("cocos", "a" * 64, "directory.read", r"F:\cocos\[3.x]")
    if expected is None:
        with pytest.raises(prompt.UserCancel):
            plugin_capabilities.prompt_grant(request)
    else:
        assert plugin_capabilities.prompt_grant(request) is expected
    rendered = output.getvalue()
    operation = i18n.t("plugin_native.operation_line", operation=i18n.t("plugin_native.operations.directory.read"))
    resource = i18n.t("plugin_native.resource_line", resource=request.resource)
    assert f" ├─ {operation}\n ├─ {resource}\n" in rendered
    assert "a" * 64 not in rendered
    assert i18n.t("plugin_native.tool_warning") not in rendered
    assert all(f"({choice})" in rendered for choice in ("1", "2", "0"))


def test_numbered_pages_keep_plain_information_rows(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", Console(file=output, width=240, force_terminal=False))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    answers = iter(["n", "2"])
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: next(answers))
    choices = [{"key": str(i), "label": str(i)} for i in range(1, 12)]
    assert prompt.select(["OpsKit"], "Choose", choices, back_label="Back",
                         info_lines=["Path: [example]"]) == "11"
    assert output.getvalue().count(" ├─ Path: [example]\n") == 2


def test_line_input_renders_information_as_plain_text(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", Console(file=output, width=240, force_terminal=False))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_read_line", lambda: "1")
    assert prompt.select_input(["OpsKit"], "Choose", [], info_lines=["Path: [example]"]) == "1"
    assert " ├─ Path: [example]\n" in output.getvalue()


def test_multiline_confirmation_uses_one_prefix_per_line(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", Console(file=output, width=240, force_terminal=False))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_read_key", lambda: "n")
    monkeypatch.setattr(prompt, "_auto_yes", False)
    assert not prompt.confirm(["OpsKit"], "Confirm", info_lines=["Name: example\nPath: location"])
    assert " ├─ Name: example\n ├─ Path: location\n" in output.getvalue()


def test_description_uses_the_same_rows(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", Console(file=output, width=240, force_terminal=False))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: "0")
    assert prompt.select(["OpsKit"], "Choose", [], description="First\nSecond") is None
    assert " ├─ First\n ├─ Second\n" in output.getvalue()


def test_long_resource_does_not_split_off_its_row_prefix(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", Console(file=output, width=32, force_terminal=False))
    monkeypatch.setattr(prompt.os, "system", lambda _: 0)
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: "0")
    resource = r"E:\some-long-path\project\client\and\more\folders"
    prompt.select(["OpsKit"], "Choose", [], info_lines=["Resource: " + resource])
    assert " ├─ Resource: " + resource + "\n" in output.getvalue()


@pytest.mark.parametrize("markup,line", [(False, "资源：[项目]"), (True, "资源：[bold]项目[/bold]")])
def test_information_text_does_not_inherit_border_color(monkeypatch, markup, line):
    captured = []
    console = Console(file=StringIO(), force_terminal=True, color_system="truecolor")
    monkeypatch.setattr(console, "print", lambda row, **kwargs: captured.append(row))
    monkeypatch.setattr(prompt, "console", console)
    prompt._render_info_lines([line], markup=markup)
    row = captured[0]
    assert row.get_style_at_offset(console, 1).color == console.get_style(prompt._PIPE_COLOR).color
    style = row.get_style_at_offset(console, 4)
    assert style.color is None and not style.dim
