import sys
from types import SimpleNamespace

import pytest

from core import i18n
from core.cli import non_interactive
from core.native_task_control import task_checkpoint


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("key", ["\x1b", "\x03"])
def test_cancel_keys_abort_task_with_localized_feedback(monkeypatch, language, key):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr("core.plugin_capabilities._interactive_input", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(kbhit=lambda: True, getwch=lambda: key))
    logs, checks = [], []
    token = non_interactive.set(False)
    try:
        check = task_checkpoint(lambda: checks.append(True), output=logs.append)
        with pytest.raises(KeyboardInterrupt):
            check()
    finally:
        non_interactive.reset(token)
    assert checks == [True]
    assert logs == [i18n.t("plugin_native.task.cancel_hint"), i18n.t("plugin_native.task.cancelled")]


@pytest.mark.parametrize("interactive", [False, True])
def test_noninteractive_never_reads_console_or_prints_hint(monkeypatch, interactive):
    monkeypatch.setattr("core.plugin_capabilities._interactive_input", lambda: interactive)
    token = non_interactive.set(True)
    original = lambda: None
    try:
        assert task_checkpoint(original, output=lambda _: pytest.fail("unexpected hint")) is original
    finally:
        non_interactive.reset(token)


def test_null_input_preserves_original_checkpoint(monkeypatch):
    monkeypatch.setattr("core.plugin_capabilities._interactive_input", lambda: False)
    token = non_interactive.set(False)
    original = lambda: None
    try:
        assert task_checkpoint(original, output=lambda _: pytest.fail("unexpected hint")) is original
    finally:
        non_interactive.reset(token)


def test_extended_keys_and_input_flood_do_not_block_authorization_checks(monkeypatch):
    monkeypatch.setattr("core.plugin_capabilities._interactive_input", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    keys = iter(["\xe0", "H"] + ["x"] * 100)
    consumed, checks = [], []
    def read():
        key = next(keys)
        consumed.append(key)
        return key
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(kbhit=lambda: True, getwch=read))
    token = non_interactive.set(False)
    try:
        check = task_checkpoint(lambda: checks.append(True), output=lambda _: None)
        check()
        assert len(consumed) == 17
        check()
        assert len(consumed) == 33 and checks == [True, True]
    finally:
        non_interactive.reset(token)
