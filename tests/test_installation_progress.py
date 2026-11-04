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
    monkeypatch.setattr(ui, 'console', SimpleNamespace(is_terminal=True))
    with pytest.raises(KeyboardInterrupt):
        with ui.InstallationProgress() as progress:
            progress('tools')
            progress('repair')
            def cancelled():
                events.append('prompt')
                raise KeyboardInterrupt
            progress.interaction(cancelled)()
    assert events == ['create', 'start', 'update', 'stop', 'prompt', 'start', 'stop']


def test_redirected_output_does_not_emit_animation_or_stage_lines(monkeypatch):
    monkeypatch.setattr(ui, 'console', SimpleNamespace(is_terminal=False))
    monkeypatch.setattr(ui, 'Status', lambda *a, **kw: pytest.fail('animation on redirected output'))
    with ui.InstallationProgress() as progress:
        for phase in ('tools', 'repair', 'fetch', 'runtime'):
            progress(phase)
