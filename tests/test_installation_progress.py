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
    monkeypatch.setattr(ui, '_InstallationStatus', Status)
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
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, color_system=None, width=100,
                      _environ={'TERM': 'xterm'})
    monkeypatch.setattr(ui, 'console', console)
    monkeypatch.setattr(theme, 'console', console)
    theme.print_warning('warning-marker')
    with ui.InstallationProgress() as progress:
        progress('fetch')
        active = output.getvalue()
        assert 'warning-marker\n\x1b[?25l\n' in active
    theme.print_error('error-marker')
    # Rich removes both transient lines before the error writes its own gap.
    assert '\x1b[?25h\r\x1b[1A\x1b[2K\x1b[1A\x1b[2K\n' in output.getvalue()


def test_spinner_colors_change_per_frame_without_coloring_the_message():
    from rich.text import Text

    spinner = ui._ColorSpinner(Text('message-marker'))
    frames = [spinner.render(index * spinner.interval / 1000 + 0.001)
              for index in range(len(spinner.frames))]
    assert len({frame.spans[0].style for frame in frames}) == len(spinner.frames)
    for frame in frames:
        assert frame.plain.endswith(' message-marker')
        assert frame.style == ''
        assert all(span.end <= 1 for span in frame.spans)


def test_resuming_after_prompt_starts_a_fresh_transient_display(monkeypatch):
    from io import StringIO
    from rich.console import Console

    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, color_system=None, width=100,
                      _environ={'TERM': 'xterm'})
    monkeypatch.setattr(ui, 'console', console)
    with ui.InstallationProgress() as progress:
        progress('fetch')
        with progress.suspend():
            console.print('prompt-marker')
        active = output.getvalue().split('prompt-marker\n', 1)[1]
        # Resuming must not move up and erase the prompt's previous lines.
        assert active.startswith('\x1b[?25l\n')


def test_redirected_output_does_not_emit_animation_or_stage_lines(monkeypatch):
    monkeypatch.setattr(ui, 'console', SimpleNamespace(is_terminal=False))
    monkeypatch.setattr(ui, '_InstallationStatus', lambda *a, **kw: pytest.fail('animation on redirected output'))
    with ui.InstallationProgress() as progress:
        for phase in ('tools', 'repair', 'fetch', 'runtime'):
            progress(phase)
