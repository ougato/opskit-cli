"""The root menu accepts an explicit host and resolves its callbacks at use time."""
from types import SimpleNamespace

import pytest

from core import main_menu, plugin_entry_preload
from core.prompt import UserCancel, UserExit


def test_root_preferences_and_exit_use_injected_host(monkeypatch):
    choices = iter(["1", "1", "2", "2", "2", None, None])
    events = []
    config = {"language": "auto"}

    def select(**kwargs):
        events.append(("screen", kwargs["breadcrumb"][-1]))
        if "on_ready" in kwargs:
            kwargs["on_ready"]()
        return next(choices)

    monkeypatch.setattr(plugin_entry_preload, "start_discovery", lambda: events.append(("preload",)))
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", lambda **kwargs: events.append(("cancel", kwargs)))
    host = SimpleNamespace(
        UserCancel=UserCancel,
        UserExit=UserExit,
        select=select,
        t=lambda key, **kwargs: key,
        get_icon=lambda name: name,
        discover_modules=lambda cfg: [],
        list_themes=lambda: ["first", "second"],
        switch_theme=lambda theme: events.append(("theme", theme)),
        i18n_switch=lambda language: events.append(("language", language)),
        _start_background_workers=lambda cfg: events.append(("background", cfg)),
        _log_startup_failure=lambda *args: events.append(("failure", *args)),
        _on_exit=lambda cfg: events.append(("exit", cfg)),
    )
    host._main_menu_loop = lambda cfg: main_menu._main_menu_loop(host, cfg)
    host._handle_preferences = lambda cfg: main_menu._handle_preferences(host, cfg)
    host._handle_theme = lambda cfg: main_menu._handle_theme(host, cfg)
    host._handle_language = lambda cfg: main_menu._handle_language(host, cfg)

    main_menu._main_menu(host, config)

    assert ("theme", "second") in events
    assert ("language", "en") in events
    assert events.count(("background", config)) == 2
    assert events.count(("preload",)) == 2
    assert ("cancel", {}) in events
    assert events[-2:] == [("cancel", {"wait": True}), ("exit", config)]
    assert not any(event[0] == "failure" for event in events)


@pytest.mark.parametrize("origin", ["keyboard", "user_exit", "normal"])
def test_root_exit_uses_one_cleanup_and_update_pass(monkeypatch, origin):
    events = []
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", lambda **kw: events.append("cancel"))
    host = SimpleNamespace(UserExit=UserExit, _on_exit=lambda cfg: events.append("update"))

    def loop(cfg):
        if origin == "keyboard":
            raise KeyboardInterrupt
        if origin == "user_exit":
            raise UserExit

    host._main_menu_loop = loop
    if origin == "normal":
        main_menu._main_menu(host, {})
    else:
        with pytest.raises(UserExit) as error:
            main_menu._main_menu(host, {})
        assert error.value.code == 0
    assert events == ["cancel", "update"]


@pytest.mark.parametrize("failure", ["cancel", "update"])
@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError])
@pytest.mark.parametrize("origin", ["normal", "user_exit", "keyboard"])
def test_exit_failure_does_not_repeat_cleanup_or_update(monkeypatch, failure, error, origin):
    events = []

    def cancel(**kwargs):
        events.append("cancel")
        if failure == "cancel":
            raise error()

    def update(cfg):
        events.append("update")
        if failure == "update":
            raise error()

    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", cancel)
    def loop(cfg):
        if origin == "user_exit":
            raise UserExit
        if origin == "keyboard":
            raise KeyboardInterrupt

    host = SimpleNamespace(UserExit=UserExit, _main_menu_loop=loop, _on_exit=update)
    with pytest.raises(error):
        main_menu._main_menu(host, {})
    assert events == (["cancel"] if failure == "cancel" else ["cancel", "update"])


def test_module_keyboard_interrupt_returns_to_root_menu(monkeypatch):
    events = []
    choices = iter(["1", None])
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", lambda **kw: events.append(("cancel", kw)))

    def entry():
        events.append("module")
        raise KeyboardInterrupt

    module = SimpleNamespace(icon="x", key="example", label="Example", entry=entry)
    host = SimpleNamespace(
        UserExit=UserExit, UserCancel=UserCancel,
        discover_modules=lambda cfg: [module], select=lambda **kwargs: next(choices),
        t=lambda key, **kwargs: key, get_icon=lambda key: key,
        _on_exit=lambda cfg: events.append("update"),
    )
    host._main_menu_loop = lambda cfg: main_menu._main_menu_loop(host, cfg)
    main_menu._main_menu(host, {})
    assert events == [("cancel", {}), "module", ("cancel", {"wait": True}), "update"]
