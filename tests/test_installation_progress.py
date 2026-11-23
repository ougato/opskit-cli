from types import SimpleNamespace
import pytest

from core import installation_progress as ui


@pytest.mark.parametrize("percentage", [None, 37])
def test_recipe_failure_keeps_only_measured_percentage(monkeypatch, percentage):
    from io import StringIO
    from rich.console import Console
    from core import progress
    from core.i18n import t

    output = StringIO()
    monkeypatch.setattr(progress, "console", Console(file=output, width=100))
    with pytest.raises(OSError):
        with progress.MultiStepProgress() as stages:
            stages.step("Download")
            if percentage is not None:
                stages.set_step_pct(percentage)
            raise OSError("download interrupted")

    rendered = output.getvalue()
    assert t("progress.failed") in rendered
    assert "50%" not in rendered
    assert ("37%" in rendered) is (percentage is not None)


def test_install_progress_uses_activity_rows_and_preserves_cancellation(monkeypatch):
    from io import StringIO
    from rich.console import Console
    from core.i18n import t

    output = StringIO()
    console = Console(file=output, force_terminal=True, force_interactive=True,
                      legacy_windows=False, width=100, _environ={'TERM': 'xterm'})
    monkeypatch.setattr(ui, 'console', console)
    with pytest.raises(KeyboardInterrupt):
        with ui.InstallationProgress() as progress:
            progress('tools')
            assert '%' not in output.getvalue()
            progress('repair')
            assert '100%' in output.getvalue()

            def cancelled():
                assert progress.display.status is None
                raise KeyboardInterrupt

            progress.interaction(cancelled)()
    assert t('progress.failed') in output.getvalue()
    assert output.getvalue().count('100%') == 1


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
    from core.i18n import t
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
        assert active.startswith('\x1b[?25l') and t('plugin.cloning') in active


def test_redirected_output_does_not_emit_animation_or_stage_lines(monkeypatch):
    monkeypatch.setattr(ui, 'console', SimpleNamespace(is_terminal=False))
    monkeypatch.setattr(ui, '_InstallationStatus', lambda *a, **kw: pytest.fail('animation on redirected output'))
    with ui.InstallationProgress() as progress:
        for phase in ('tools', 'repair', 'fetch', 'runtime'):
            progress(phase)
