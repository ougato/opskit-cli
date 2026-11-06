"""The documented starter is a working SDK 4 plugin in both languages."""
import importlib.util
from pathlib import Path

import pytest

from core import i18n, sdk
from core.plugin import load_manifest


EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "example-plugin"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_example_manifest_menu_and_cli(monkeypatch, capsys, language):
    manifest = load_manifest(EXAMPLE)
    assert manifest is not None and manifest.api_version == 4 and manifest.entry == "example_plugin"
    monkeypatch.setattr(i18n, "_extra", {})
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(i18n, "_lang", language)
    i18n.init(language=language)
    screens, messages = [], []
    answers = iter(["1", None])
    def select(*args, **kwargs):
        screens.append((args, kwargs))
        return next(answers)
    monkeypatch.setattr(sdk, "select", select)
    monkeypatch.setattr(sdk, "print_success", lambda text: messages.append(text))
    monkeypatch.setattr(sdk, "print_error", lambda text: messages.append(text))
    spec = importlib.util.spec_from_file_location("example_test", EXAMPLE / "example_plugin" / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register().entry()
    assert len(screens) == 2 and len(messages) == 1
    assert screens[0][1]["back_label"].endswith("返回" if language == "zh" else "Back")
    assert screens[0][1]["info_lines"] == ["操作：运行示例任务" if language == "zh" else "Operation: Run the example task"]
    assert module.cli(["--hello"]) == 0 and len(messages) == 2
    assert module.cli(["--help"]) == 0 and "--hello" in capsys.readouterr().out
    assert module.cli(["--invalid"]) == 2
    assert all("example." not in message for message in messages)
