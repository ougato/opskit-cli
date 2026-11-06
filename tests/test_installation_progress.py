from types import SimpleNamespace
import pytest

from core import installation_progress as ui


def test_one_status_is_updated_suspended_and_closed_on_cancel(monkeypatch):
    events = []
    class Status:
        def __init__(self, *args, **kwargs):
            events.append('create')
        def start(self):
            events.append('start')
        def stop(self):
            events.append('stop')
        def update(self, message):
            events.append('update')
    monkeypatch.setattr(ui, 'Status', Status)
    monkeypatch.setattr(ui, 'console', SimpleNamespace(is_terminal=True, print=lambda: events.append('blank')))
    with pytest.raises(KeyboardInterrupt):
        with ui.InstallationProgress() as progress:
            progress('tools')
            progress('repair')
            def cancelled():
                events.append('prompt')
                raise KeyboardInterrupt
            progress.interaction(cancelled)()
    assert events == ['create', 'start', 'update', 'stop', 'prompt', 'start', 'stop']


def test_install_warning_and_error_have_one_blank_line(monkeypatch):
    from io import StringIO
    from rich.console import Console
    from core import theme

    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system=None, width=100)
    class Status:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def stop(self):
            pass
    monkeypatch.setattr(ui, 'console', console)
    monkeypatch.setattr(theme, 'console', console)
    monkeypatch.setattr(ui, 'Status', Status)
    theme.print_warning('warning-marker')
    with ui.InstallationProgress() as progress:
        progress('fetch')
    theme.print_error('error-marker')
    assert output.getvalue().split('warning-marker', 1)[1].split('error-marker', 1)[0].count('\n') == 2


def test_redirected_output_does_not_emit_animation_or_stage_lines(monkeypatch):
    monkeypatch.setattr(ui, 'console', SimpleNamespace(is_terminal=False))
    monkeypatch.setattr(ui, 'Status', lambda *a, **kw: pytest.fail('animation on redirected output'))
    with ui.InstallationProgress() as progress:
        for phase in ('tools', 'repair', 'fetch', 'runtime'):
            progress(phase)
