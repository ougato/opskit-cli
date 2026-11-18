"""Discovery failures remain visible without breaking other startup work."""
from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

from core import builtins, loader
from core.module import ModuleInfo


def _module(key):
    return ModuleInfo(key=key, description_key="", order=1, entry=lambda: None)


@pytest.mark.parametrize("language,expected", [
    ("zh", "无法访问或创建配置"),
    ("en", "Cannot access or create configuration"),
])
def test_configuration_io_failure_is_localized_without_leaking_details(monkeypatch, language, expected):
    import main
    from core import cli, i18n
    from typer.testing import CliRunner

    def fail():
        raise PermissionError("PRIVATE-PATH-DIAGNOSTIC")

    monkeypatch.setattr(main, "ensure_config", fail)
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    result = CliRunner().invoke(main.app, ["--lang", language])
    assert result.exit_code != 0
    assert expected in result.output
    assert "PRIVATE-PATH-DIAGNOSTIC" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("language,expected", [
    ("zh", "配置正被其他操作使用"),
    ("en", "Configuration is being used by another operation"),
])
def test_configuration_lock_contention_is_localized(monkeypatch, language, expected):
    import main
    from core import cli, i18n
    from core.resource_lock import ResourceBusyError
    from typer.testing import CliRunner

    def fail():
        raise ResourceBusyError("PRIVATE-PATH-DIAGNOSTIC")

    monkeypatch.setattr(main, "ensure_config", fail)
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    result = CliRunner().invoke(main.app, ["--lang", language])
    assert result.exit_code != 0
    assert expected in result.output
    assert "PRIVATE-PATH-DIAGNOSTIC" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("language,expected", [
    ("zh", "无法确认配置锁文件的身份"),
    ("en", "Cannot verify the configuration lock file"),
])
def test_configuration_lock_identity_failure_is_localized(monkeypatch, language, expected):
    import main
    from core import cli, i18n
    from core.resource_lock import ResourceIdentityError
    from typer.testing import CliRunner

    def fail():
        raise ResourceIdentityError("PRIVATE-PATH-DIAGNOSTIC")

    monkeypatch.setattr(main, "ensure_config", fail)
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    result = CliRunner().invoke(main.app, ["--lang", language])
    assert result.exit_code != 0
    assert expected in result.output
    assert "PRIVATE-PATH-DIAGNOSTIC" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("failure", ["import", "register", "missing", "invalid"])
def test_bad_module_does_not_hide_healthy_module_or_leak_errors(monkeypatch, failure):
    log = Mock()
    monkeypatch.setattr(loader, "_log", log)
    monkeypatch.setattr(loader, "_is_frozen", lambda: False)
    monkeypatch.setattr(builtins, "MODULES", (("bad", "bad"), ("good", "good")))

    def broken():
        raise RuntimeError("SECRET-should-not-appear")

    def load(package):
        if package == "good":
            return SimpleNamespace(register=lambda: _module("good"))
        if failure == "import":
            broken()
        if failure == "register":
            return SimpleNamespace(register=broken)
        if failure == "missing":
            return SimpleNamespace()
        return SimpleNamespace(register=lambda: "invalid")

    monkeypatch.setattr(loader.importlib, "import_module", load)
    found = loader.discover_modules()
    assert [item.key for item in found] == ["good"]
    assert found.failures == ("bad",)
    assert "SECRET-should-not-appear" not in str(log.warning.call_args_list)
    assert "bad" in str(log.warning.call_args_list)


@pytest.mark.parametrize("field, value", [
    ("key", "other"),
    ("description_key", None),
    ("enabled", "false"),
    ("label", object()),
    ("icon", object()),
    ("order", True),
])
@pytest.mark.parametrize("frozen", [False, True])
def test_invalid_module_fields_are_isolated_before_the_menu(monkeypatch, field, value, frozen):
    monkeypatch.setattr(loader, "_is_frozen", lambda: frozen)
    monkeypatch.setattr(builtins, "MODULES", (("bad", "bad"), ("good", "good")))
    bad = _module("bad")
    setattr(bad, field, value)
    def load(package):
        if package == "_registry":
            return SimpleNamespace(MODULE_LIST=[("bad", "bad"), ("good", "good")])
        return SimpleNamespace(register=lambda: bad if package == "bad" else _module("good"))
    monkeypatch.setattr(loader.importlib, "import_module", load)

    found = loader.discover_modules({"modules": {"other": {"enabled": False}}})
    assert [item.key for item in found] == ["good"]
    assert found.failures == ("bad",)


def test_malformed_label_does_not_prevent_root_menu(monkeypatch):
    import main
    from core import plugin_entry_preload

    class InvalidLabel:
        def __format__(self, spec):
            raise RuntimeError("bad label")

    bad = _module("bad")
    bad.label = InvalidLabel()
    monkeypatch.setattr(loader, "_is_frozen", lambda: False)
    monkeypatch.setattr(builtins, "MODULES", (("bad", "bad"), ("good", "good")))
    monkeypatch.setattr(loader.importlib, "import_module", lambda package: SimpleNamespace(
        register=lambda: bad if package == "bad" else _module("good")))
    monkeypatch.setattr(main, "discover_modules", loader.discover_modules)
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", lambda **kwargs: None)
    monkeypatch.setattr(main, "_on_exit", lambda cfg: None)
    rendered = []
    monkeypatch.setattr(main, "select", lambda **kwargs: rendered.append(kwargs) or None)

    main._main_menu({})

    assert len(rendered) == 1
    assert len(rendered[0]["choices"]) == 2  # good module and preferences
    assert rendered[0]["info_lines"]


def test_disabled_module_is_not_loaded_or_reported(monkeypatch):
    monkeypatch.setattr(loader, "_is_frozen", lambda: False)
    monkeypatch.setattr(builtins, "MODULES", (("disabled", "disabled"),))
    monkeypatch.setattr(loader.importlib, "import_module", lambda package: pytest.fail("disabled module loaded"))
    found = loader.discover_modules({"modules": {"disabled": {"enabled": False}}})
    assert found == []
    assert found.failures == ()


def test_inaccessible_log_cannot_hide_healthy_modules(monkeypatch):
    monkeypatch.setattr(loader, "_is_frozen", lambda: False)
    monkeypatch.setattr(builtins, "MODULES", (("bad", "bad"), ("good", "good")))
    monkeypatch.setattr(loader, "_log", SimpleNamespace(
        warning=lambda *args: (_ for _ in ()).throw(PermissionError("log denied"))))
    monkeypatch.setattr(loader.importlib, "import_module", lambda package: (
        SimpleNamespace(register=lambda: _module("good")) if package == "good"
        else (_ for _ in ()).throw(RuntimeError("secret"))))
    found = loader.discover_modules()
    assert [item.key for item in found] == ["good"]
    assert found.failures == ("bad",)


@pytest.mark.parametrize("kind", ["missing_registry", "missing_dependency", "invalid"])
def test_frozen_registry_fallback_distinguishes_absence_from_failure(monkeypatch, kind):
    monkeypatch.setattr(loader, "_is_frozen", lambda: True)
    monkeypatch.setattr(builtins, "MODULES", (("good", "good"),))
    monkeypatch.setattr(loader, "_log", Mock())

    def load(package):
        if package == "good":
            return SimpleNamespace(register=lambda: _module("good"))
        if kind == "missing_registry":
            raise ModuleNotFoundError("registry missing", name="_registry")
        if kind == "missing_dependency":
            raise ModuleNotFoundError("private error", name="dependency")
        return SimpleNamespace(MODULE_LIST=None)

    monkeypatch.setattr(loader.importlib, "import_module", load)
    found = loader.discover_modules()
    assert [item.key for item in found] == ["good"]
    assert found.failures == (() if kind == "missing_registry" else ("_registry",))


@pytest.mark.parametrize("language, expected", [
    ("zh", "部分内置模块加载失败：软件管理"),
    ("en", "Some built-in modules failed to load: Software"),
])
def test_failure_message_survives_menu_clear(monkeypatch, language, expected):
    import main
    from core import cli, i18n, plugin_entry_preload, prompt

    output = StringIO()
    monkeypatch.setattr(i18n, "_current", i18n._current.copy())
    monkeypatch.setattr(i18n, "_core_keys", i18n._core_keys.copy())
    monkeypatch.setattr(i18n, "_lang", i18n._lang)
    i18n.init(language=language)
    monkeypatch.setattr(prompt, "console", Console(file=output, width=160, color_system=None))
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: "0")
    monkeypatch.setattr(prompt.os, "system", lambda cmd: output.write("CLEAR\n"))
    monkeypatch.setattr(main, "select", prompt.select)
    monkeypatch.setattr(main, "discover_modules", lambda cfg: loader.ModuleDiscovery([], ["software"]))
    monkeypatch.setattr(main, "_start_background_workers", lambda cfg: None)
    monkeypatch.setattr(plugin_entry_preload, "start_discovery", lambda: None)
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", lambda **kwargs: None)
    monkeypatch.setattr(main, "_on_exit", lambda cfg: None)
    main._main_menu({})
    rendered = output.getvalue()
    assert rendered.index("CLEAR") < rendered.index(expected)


def test_worker_failures_are_isolated_and_retry_only_failed_tasks(monkeypatch):
    import main
    import core.mirror as mirror
    import core.telemetry as telemetry
    import core.updater as updater
    import core.version_cache as version_cache

    calls = []
    logs = Mock()
    monkeypatch.setattr(main, "_startup_log", logs)
    monkeypatch.setattr(main, "_bg_started", set())

    class Thread:
        def __init__(self, *, target, daemon, name):
            self.target, self.name = target, name

        def start(self):
            if self.name == "opskit-mirror" and "mirror-start" not in calls:
                calls.append("mirror-start")
                raise RuntimeError("private start error")
            self.target()

    monkeypatch.setattr(main._threading, "Thread", Thread)
    monkeypatch.setattr(mirror, "init", lambda: calls.append("mirror"))
    monkeypatch.setattr(version_cache, "refresh_all_background", lambda: calls.append("vcache"))
    monkeypatch.setattr(updater, "check_update_background", lambda cfg: calls.append("updater"))
    monkeypatch.setattr(telemetry, "init", lambda cfg: calls.append("telemetry"))

    def health(delay):
        calls.append("health")
        if calls.count("health") == 1:
            raise ValueError("private health error")

    monkeypatch.setattr(updater, "confirm_health_after", health)
    main._start_background_workers({})
    assert calls == ["mirror-start", "vcache", "updater", "telemetry", "health"]
    main._start_background_workers({})
    assert calls == ["mirror-start", "vcache", "updater", "telemetry", "health", "mirror", "health"]
    main._start_background_workers({})
    assert len(calls) == 7
    assert "private" not in str(logs.warning.call_args_list)


def test_log_failure_does_not_interrupt_startup_diagnostics(monkeypatch):
    import main
    monkeypatch.setattr(main, "_startup_log", SimpleNamespace(
        warning=lambda *args: (_ for _ in ()).throw(PermissionError("log denied"))))
    main._log_startup_failure("pending_update", ValueError("private"))


def test_failed_background_start_does_not_skip_plugin_discovery(monkeypatch):
    import main
    from core import plugin_entry_preload

    calls = []
    monkeypatch.setattr(main, "discover_modules", lambda cfg: [])
    monkeypatch.setattr(main, "_startup_log", Mock())
    monkeypatch.setattr(main, "_start_background_workers", lambda cfg: (_ for _ in ()).throw(RuntimeError("secret")))
    monkeypatch.setattr(plugin_entry_preload, "start_discovery", lambda: calls.append("discovery"))
    monkeypatch.setattr(plugin_entry_preload, "cancel_pending", lambda **kwargs: None)
    monkeypatch.setattr(main, "_on_exit", lambda cfg: None)

    def select(**kwargs):
        kwargs["on_ready"]()
        return None

    monkeypatch.setattr(main, "select", select)
    main._main_menu({})
    assert calls == ["discovery"]
    assert "secret" not in str(main._startup_log.warning.call_args_list)


def test_exit_update_failure_is_diagnosed_without_exposing_error(monkeypatch):
    import main
    from core import updater

    logs = Mock()
    monkeypatch.setattr(main, "_startup_log", logs)
    monkeypatch.setattr(updater, "has_pending_update", lambda: False)
    monkeypatch.setattr(updater, "apply_pending_update", lambda: (
        _ for _ in ()).throw(RuntimeError("sensitive update path")))
    main._on_exit({})
    logs.warning.assert_called_once_with(
        "startup task failed: task=%s error=%s", "exit_update", "RuntimeError"
    )
    assert "sensitive" not in str(logs.warning.call_args_list)


@pytest.mark.parametrize("log_fails", [False, True])
def test_ready_failure_is_diagnosed_and_menu_remains_usable(monkeypatch, log_fails):
    import logging
    from core import cli, prompt

    log = logging.getLogger("opskit.startup")
    warning = Mock()
    if log_fails:
        warning.side_effect = PermissionError("log denied")
    monkeypatch.setattr(log, "warning", warning)
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    monkeypatch.setattr(prompt.os, "system", lambda command: 0)
    monkeypatch.setattr(prompt, "console", Console(file=StringIO(), width=100))
    monkeypatch.setattr(prompt, "_read_key_seq", lambda: "1")

    def fail_ready():
        raise RuntimeError("sensitive callback data")

    assert prompt.select(["OpsKit"], "", [{"key": "1", "label": "one"}],
                         on_ready=fail_ready) == "1"
    warning.assert_called_once_with("menu ready callback failed: error=%s", "RuntimeError")
    assert "sensitive" not in str(warning.call_args_list)
