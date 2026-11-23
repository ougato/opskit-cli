"""The shared wait indicator is transient, supervised and non-authorizing."""
from types import SimpleNamespace

import pytest

from core import installation_progress, plugin_capabilities, plugin_sandbox, plugin_worker, prompt, sdk
from core.cli import non_interactive
from core.native_sandbox import CapabilityRequest, GrantChoice


def test_wait_display_nests_suspends_and_cleans_up(monkeypatch):
    events = []
    class Status:
        def __init__(self, message, **kwargs):
            events.append(("create", message.plain))
        def start(self):
            events.append(("start",))
        def stop(self):
            events.append(("stop",))
        def update(self, message):
            events.append(("update", message.plain))
    monkeypatch.setattr(installation_progress, "_InstallationStatus", Status)
    display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=True))
    display.start("first")
    display.start("second")
    with display.suspend():
        with display.suspend():
            assert display.status is None
    display.stop()
    display.close()
    assert events == [("create", "first"), ("start",), ("update", "second"), ("stop",),
                      ("create", "second"), ("start",), ("update", "first"), ("stop",)]


def test_nested_plugin_wait_cleanup_preserves_caller(monkeypatch):
    display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=False))
    display.start("caller")
    monkeypatch.setattr(plugin_sandbox._invocations, "wait_display", display, raising=False)
    monkeypatch.setattr(plugin_sandbox._invocations, "wait_floor", display.depth, raising=False)
    monkeypatch.setattr(plugin_sandbox, "check_authorization", lambda _: "fingerprint")
    monkeypatch.setattr(plugin_sandbox, "_remaining", lambda: 1)

    with pytest.raises(plugin_sandbox.SandboxError, match="no active waiting scope"):
        plugin_sandbox._broker(None, {
            "rpc_version": plugin_sandbox.plugin_json.RPC_VERSION,
            "method": "ui.wait_stop",
        })

    display.start("service")
    display.restore(1)
    assert display.depth == 1
    assert display.messages[0].label == "caller"


@pytest.mark.parametrize("terminal,noninteractive", [(False, False), (True, True)])
def test_wait_display_is_silent_without_interactive_terminal(monkeypatch, terminal, noninteractive):
    monkeypatch.setattr(installation_progress, "_InstallationStatus", lambda *a, **k: pytest.fail("unexpected animation"))
    token = non_interactive.set(noninteractive)
    try:
        display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=terminal))
        display.start()
        display.stop()
        display.close()
    finally:
        non_interactive.reset(token)


def test_authorization_suspends_wait_and_resumes_after_prompt(tmp_path, monkeypatch):
    events = []
    class Status:
        def __init__(self, *a, **k): pass
        def start(self): events.append("start")
        def stop(self): events.append("stop")
        def update(self, message): pass
    monkeypatch.setattr(installation_progress, "_InstallationStatus", Status)
    display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=True))
    display.start()
    authority = plugin_capabilities.CapabilityAuthority(path=tmp_path / "grants.yaml")
    authority.suspend_wait = display.suspend
    request = CapabilityRequest("demo", "a" * 64, "directory.read", str(tmp_path))
    def ask(_):
        assert display.status is None
        events.append("prompt")
        return GrantChoice.ONCE
    authority.require(request, check_identity=lambda *_: True, prompt=ask)
    display.close()
    assert events == ["start", "stop", "prompt", "start", "stop"]


def test_worker_wait_scope_uses_host_broker_even_when_task_fails(monkeypatch):
    from core import paths
    for name in sdk.__all__:
        monkeypatch.setattr(sdk, name, getattr(sdk, name))
    for name in ("select", "select_input", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"):
        monkeypatch.setattr(prompt, name, getattr(prompt, name))
    for name in ("plugin_data_dir", "plugins_dir"):
        monkeypatch.setattr(paths, name, getattr(paths, name))
    calls = []
    monkeypatch.setattr(plugin_worker, "broker", lambda method, *args, **kwargs: calls.append((method, args)))
    plugin_worker._adapt_sdk("demo")
    with pytest.raises(ValueError, match="task failed"):
        with sdk.waiting("working"):
            raise ValueError("task failed")
    assert calls == [("ui.wait_start", ("working",)), ("ui.wait_stop", ())]


def test_broker_wait_requests_are_validated_and_cannot_leave_unbalanced_scope(monkeypatch):
    display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=False))
    monkeypatch.setattr(plugin_sandbox._invocations, "wait_display", display, raising=False)
    monkeypatch.setattr(plugin_sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(plugin_sandbox, "_remaining", lambda *a: 100)
    manifest = SimpleNamespace()
    def call(method, *args):
        return plugin_sandbox._broker(manifest, {"rpc_version": 1, "method": method, "args": list(args)})
    call("ui.wait_start", "")
    assert [step.label for step in display.messages] == [""]
    call("ui.wait_stop")
    assert display.messages == []
    with pytest.raises(ValueError):
        call("ui.wait_start", "bad\nmessage")
    with pytest.raises(ValueError):
        call("ui.wait_stop")
    with pytest.raises(plugin_sandbox.SandboxError):
        call("ui.wait_start")
    display.close()


def test_broker_suspends_wait_while_rendering_a_menu(monkeypatch):
    events = []
    class Status:
        def __init__(self, *a, **k): pass
        def start(self): events.append("start")
        def stop(self): events.append("stop")
        def update(self, message): pass
    monkeypatch.setattr(installation_progress, "_InstallationStatus", Status)
    display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=True))
    monkeypatch.setattr(plugin_sandbox._invocations, "wait_display", display, raising=False)
    monkeypatch.setattr(plugin_sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(plugin_sandbox, "_remaining", lambda *a: 100)
    monkeypatch.setattr(plugin_sandbox, "_check_live_authorizations", lambda **kw: None)
    def select(*args, **kwargs):
        assert display.status is None
        events.append("menu")
        return "1"
    monkeypatch.setattr(prompt, "select", select)
    display.start()
    assert plugin_sandbox._broker(SimpleNamespace(name="demo"), {"rpc_version": 1, "method": "select", "args": []}) == "1"
    display.close()
    assert events == ["start", "stop", "menu", "start", "stop"]


def test_upload_batches_report_cumulative_verified_files(monkeypatch):
    from core import plugin_upload

    display = installation_progress.WaitingDisplay(SimpleNamespace(is_terminal=False))
    monkeypatch.setattr(plugin_sandbox._invocations, "wait_display", display, raising=False)
    monkeypatch.setattr(plugin_sandbox._invocations, "capability_authority", object(), raising=False)
    monkeypatch.setattr(plugin_sandbox, "check_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(plugin_sandbox, "_remaining", lambda: 100)
    monkeypatch.setattr(plugin_sandbox, "_read_sandbox_setting", lambda *_: [])
    seen = []

    def transfer(*args, console, on_verified, **kwargs):
        assert console is None
        for count in (1, 2):
            on_verified(count)
            seen.append(display.messages[-1].completed)

    monkeypatch.setattr(plugin_upload, "upload_files", transfer)
    manifest = SimpleNamespace(name="demo", permissions=["network"])
    display.start("Uploading", total=4, failed_label="Failed")
    for offset in (0, 2):
        plugin_sandbox._broker(manifest, {"rpc_version": 1, "method": "host.upload_files",
                                        "args": ["folder", []], "kwargs": {"progress_offset": offset}})
    assert seen == [1, 2, 3, 4]
    display.close()


def test_wait_display_renders_and_removes_transient_spinner(monkeypatch):
    from io import StringIO
    from rich.console import Console
    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, color_system="truecolor", width=100,
                      _environ={"TERM": "xterm"})
    display = installation_progress.WaitingDisplay(console)
    from core import prompt
    monkeypatch.setattr(prompt, "console", console)
    prompt.print_header(["OpsKit", "Update", "Storage Hub"])
    display.start()
    active = output.getvalue()
    display.close()
    assert active.count("\n") == 2 and "⠋" in active
    assert "\x1b[?25h" in output.getvalue()


@pytest.mark.parametrize("text_input", [False, True])
def test_waiting_separator_after_menu_input(monkeypatch, text_input):
    from io import StringIO
    from rich.console import Console

    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, width=100, _environ={"TERM": "xterm"})
    monkeypatch.setattr(prompt, "console", console)
    token = prompt._menu_visible.set(False)
    display = installation_progress.WaitingDisplay(console)
    try:
        if text_input:
            monkeypatch.setattr(prompt, "_read_line", lambda **kwargs: "1.8.0")
            monkeypatch.setattr(console, "clear", lambda: None)
            assert prompt.text_input(["OpsKit", "Build"], "Version") == "1.8.0"
        else:
            prompt._render_options([("4", "Plugins")])
            console.print("4")
        offset = len(output.getvalue())
        display.start("Read installed plugins")
        active = output.getvalue()[offset:]
        assert active.count("\n") == 1 and "Read installed plugins" in active
    finally:
        display.close()
        prompt._menu_visible.reset(token)


def test_waiting_separator_after_completed_progress(monkeypatch):
    from io import StringIO
    from rich.console import Console

    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, width=100, _environ={"TERM": "xterm"})
    monkeypatch.setattr(installation_progress, "console", console)
    with installation_progress.InstallationProgress() as progress:
        progress("update_wait")
        with progress.copy_files(1) as update:
            update(1)
        assert progress.display.after_progress
        assert "100%" in output.getvalue()

    display = installation_progress.WaitingDisplay(console)
    display.start("Copy", failed_label="Failed", total=1)
    display.update(1)
    display.stop()
    offset = len(output.getvalue())
    display.start("Prepare")
    active = output.getvalue()[offset:]
    display.close()
    assert active.count("\n") == 1 and "Prepare" in active


def test_counted_progress_rejects_invalid_updates_and_requires_all_items():
    from io import StringIO
    from rich.console import Console
    output = StringIO()
    display = installation_progress.WaitingDisplay(Console(file=output))
    display.start("Download", failed_label="Failed", total=2)
    display.update(1)
    with pytest.raises(ValueError):
        display.update(0)
    with pytest.raises(ValueError):
        display.update(3)
    display.stop()
    assert "Failed" in output.getvalue() and "50%" in output.getvalue()
    assert "100%" not in output.getvalue()

    display.start("Download", failed_label="Failed", total=2)
    display.update(2)
    display.stop()
    assert "100%" in output.getvalue()


def test_counted_progress_renders_real_intermediate_state_in_terminal():
    from io import StringIO
    from rich.console import Console

    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, color_system="truecolor", width=100,
                      _environ={"TERM": "xterm"})
    display = installation_progress.WaitingDisplay(console)
    display.start("Verify files", failed_label="Failed", total=4)
    try:
        assert "0%" in output.getvalue()
        display.update(2)
        display.status.refresh()
        assert "50%" in output.getvalue()
        display.stop(failed=True)
    finally:
        display.close()
    rendered = output.getvalue()
    assert "Failed" in rendered and "100%" not in rendered
    assert "\x1b[?25h" in rendered


def test_broker_keeps_counted_progress_separate_from_waiting(monkeypatch):
    from io import StringIO
    from rich.console import Console

    output = StringIO()
    display = installation_progress.WaitingDisplay(Console(file=output))
    monkeypatch.setattr(plugin_sandbox._invocations, "wait_display", display, raising=False)
    monkeypatch.setattr(plugin_sandbox, "check_authorization", lambda _: "fingerprint")
    monkeypatch.setattr(plugin_sandbox, "_remaining", lambda: 1)

    def call(method, *args, **kwargs):
        return plugin_sandbox._broker(None, {
            "rpc_version": plugin_sandbox.plugin_json.RPC_VERSION,
            "method": method, "args": list(args), "kwargs": kwargs,
        })

    with pytest.raises(plugin_sandbox.SandboxError):
        call("ui.wait_start", "Download", total=2, failed_label="Failed")
    with pytest.raises(plugin_sandbox.SandboxError):
        call("ui.progress_start", "Download", failed_label="Failed")
    call("ui.progress_start", "Download", total=2, failed_label="Failed")
    with pytest.raises(plugin_sandbox.SandboxError):
        call("ui.wait_stop")
    call("ui.progress_update", 2)
    call("ui.progress_stop")
    assert "100%" in output.getvalue()
    assert display.depth == 0

@pytest.mark.parametrize("failed", [False, True])
def test_activity_progress_keeps_result_without_estimated_percentage(failed):
    from io import StringIO
    from rich.console import Console

    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, width=100, _environ={"TERM": "xterm"})
    display = installation_progress.WaitingDisplay(console)
    display.start("Check cache", failed_label="Failed")
    assert display.progressing and not display.counting
    assert "Check cache" in output.getvalue()
    assert "%" not in output.getvalue()
    with pytest.raises(ValueError, match="invalid progress count"):
        display.update(1)
    display.stop(failed=failed)
    assert ("Failed" in output.getvalue()) == failed
    assert ("100%" in output.getvalue()) != failed
