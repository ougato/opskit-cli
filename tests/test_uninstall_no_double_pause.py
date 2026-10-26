"""Uninstall menus own one final pause, regardless of installation wizard support.

Recipes may still pause when called directly. Their pauses are suppressed only
inside menu-owned uninstall operations, so success and errors stay visible once.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

import core.prompt as prompt
from core.cli import configure
import software.menu as menu


class _FakeInstance:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    def detect(self):
        return "installed"

    def uninstall(self, version=None):
        if not self._ok:
            raise RuntimeError("boom")


def _make_cls(*, has_wizard: bool):
    return type(
        "FakeRecipe",
        (),
        {
            "key": "faketool",
            "description": "Fake Tool",
            "has_wizard": has_wizard,
            "has_switch": False,
            "confirm_before_uninstall": True,
        },
    )


def _patch_common(monkeypatch, pause_counter: list[int]):
    monkeypatch.setattr(menu, "pause", lambda *a, **k: pause_counter.append(1))
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(menu, "clear_screen", lambda *a, **k: None)
    monkeypatch.setattr(menu, "print_header", lambda *a, **k: None)
    monkeypatch.setattr(menu, "print_success", lambda *a, **k: None)
    monkeypatch.setattr(menu, "print_warning", lambda *a, **k: None)
    monkeypatch.setattr(menu, "report_failure", lambda *a, **k: None)
    monkeypatch.setattr(menu.base_console, "print", lambda *a, **k: None)


def test_wizard_uninstall_success_frame_pauses_once(monkeypatch):
    calls: list[int] = []
    _patch_common(monkeypatch, calls)
    cls = _make_cls(has_wizard=True)
    menu._do_uninstall(["OpsKit"], cls, _FakeInstance(ok=True))
    assert calls == [1], "向导型 recipe 卸载成功后也必须保留结果"


def test_non_wizard_uninstall_success_frame_pauses_once(monkeypatch):
    calls: list[int] = []
    _patch_common(monkeypatch, calls)
    cls = _make_cls(has_wizard=False)
    menu._do_uninstall(["OpsKit"], cls, _FakeInstance(ok=True))
    assert calls == [1], "非向导型 recipe 卸载成功时框架应 pause 恰好一次"


def test_uninstall_failure_frame_pauses_once(monkeypatch):
    for has_wizard in (True, False):
        calls: list[int] = []
        _patch_common(monkeypatch, calls)
        cls = _make_cls(has_wizard=has_wizard)
        menu._do_uninstall(["OpsKit"], cls, _FakeInstance(ok=False))
        assert calls == [1], "卸载失败时框架应 pause 恰好一次以展示错误"


@pytest.fixture
def terminal(monkeypatch):
    events = []
    _patch_common(monkeypatch, [])
    configure("text", False)
    monkeypatch.setattr(prompt, "_auto_yes", False)
    monkeypatch.setattr(prompt, "_drain_input", lambda: None)
    monkeypatch.setattr(prompt, "_read_key", lambda: events.append("keypress"))
    monkeypatch.setattr(menu, "pause", lambda: prompt.pause("press any key"))
    monkeypatch.setattr(menu, "report_failure", lambda *args, **kwargs: events.append("error"))
    # Avoid changing the process's real signal handlers during a fake read.
    with prompt.guard_input(lambda: None):
        yield events
    configure("text", False)


@pytest.mark.parametrize("wizard", [False, True])
@pytest.mark.parametrize("internal_pause", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_result_is_visible_before_exactly_one_keypress(terminal, wizard, internal_pause, success):
    class Recipe(_FakeInstance):
        def uninstall(self, version=None):
            terminal.append("uninstall")
            if internal_pause:
                # Alias imported by a recipe must see the scoped suppression too.
                prompt.pause("recipe completion")
            if not success:
                raise RuntimeError("synthetic uninstall failure")
            terminal.append("done")
    menu._do_uninstall(["OpsKit"], _make_cls(has_wizard=wizard), Recipe())
    assert terminal == ["uninstall", "done" if success else "error", "keypress"]


def test_multiversion_uninstall_waits_once_after_internal_pause(terminal, monkeypatch):
    cls = _make_cls(has_wizard=False)
    cls.has_switch = True
    class Recipe(_FakeInstance):
        def installed_versions(self):
            return ["1.0", "2.0"]
        def uninstall(self, version=None):
            terminal.append(version)
            prompt.pause("internal pause")
    monkeypatch.setattr(menu, "select", lambda **kwargs: "1")
    menu._do_uninstall(["OpsKit"], cls, Recipe())
    assert terminal == ["1.0", "keypress"]


def test_legacy_uninstall_menu_does_not_double_pause(terminal, monkeypatch):
    from software import registry
    from core import platform, installed_cache, privilege
    class Recipe(_FakeInstance):
        key = "fake"
        platforms = ["linux"]
        description = "Fake"
        def uninstall(self, version=None):
            terminal.append("uninstall")
            prompt.pause("internal pause")
    monkeypatch.setattr(registry, "all_recipes", lambda: [Recipe])
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(installed_cache, "get_detected", lambda cls: "1.0")
    monkeypatch.setattr(privilege, "ensure_root_for_action", lambda *args: None)
    monkeypatch.setattr(menu, "select", lambda **kwargs: "1")
    menu.show_uninstall()
    assert terminal == ["uninstall", "keypress"]


def test_pause_suppression_is_nested_and_restored_after_exception(terminal):
    with prompt.suppress_pause():
        with pytest.raises(RuntimeError):
            with prompt.suppress_pause():
                prompt.pause("inner")
                raise RuntimeError("synthetic")
        prompt.pause("outer")
    assert terminal == []
    prompt.pause("final")
    assert terminal == ["keypress"]


@pytest.mark.parametrize("mode", ["noninteractive", "yes"])
def test_automation_does_not_wait_for_keypress(terminal, monkeypatch, mode):
    if mode == "noninteractive":
        configure("json", True)
    else:
        monkeypatch.setattr(prompt, "_auto_yes", True)
    menu._do_uninstall(["OpsKit"], _make_cls(has_wizard=True), _FakeInstance())
    assert terminal == []


def test_cancelled_uninstall_does_not_wait(terminal, monkeypatch):
    monkeypatch.setattr(menu, "confirm", lambda **kwargs: False)
    menu._do_uninstall(["OpsKit"], _make_cls(has_wizard=False), _FakeInstance())
    assert terminal == []
