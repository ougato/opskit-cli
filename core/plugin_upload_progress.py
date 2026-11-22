"""Shared transfer display for host-backed plugin uploads."""
from collections import deque
from contextlib import contextmanager
from time import monotonic

from rich.filesize import decimal
from rich.live import Live
from rich.text import Text

from core.i18n import t
from core.progress import _GREEN, _RED, _global_step_width, _render_line
from core.theme import get_color


@contextmanager
def upload_progress(console, total, files, breadcrumb=None):
    if console is None:
        yield lambda *args: None
        return

    started = monotonic()
    samples = deque([(started, 0)])
    state = (0, 0, 0)
    width = _global_step_width()
    label = t("plugin_native.upload.title")

    def update(sent, verified, completed):
        nonlocal state
        state = (sent, verified, completed)

    def row(color, *, failed=False):
        now = monotonic()
        sent, verified, completed = state
        samples.append((now, sent))
        while len(samples) > 1 and samples[1][0] <= now - 2:
            samples.popleft()
        since, previous = samples[0]
        rate = decimal(round((sent - previous) / max(now - since, 0.001)))
        elapsed = now - started
        percentage = (verified * 100 // total) if total else (completed * 100 // files)
        if completed < files:
            percentage = min(99, percentage)
        text = Text.from_markup(_render_line(label, percentage, elapsed, color, width))
        text.append(f"  {completed}/{files}  {rate}/s", style=color)
        if failed:
            text.append("  " + t("plugin_native.upload.failed"), style=color)
        return text

    from core.cli import non_interactive
    from core.prompt import clear_screen, print_header
    if not console.is_terminal or non_interactive.get():
        yield update
        return
    clear_screen()
    print_header(breadcrumb or [t("plugin_native.upload.header")])
    console.print()
    try:
        with Live(console=console, transient=True, refresh_per_second=10,
                  redirect_stdout=False, redirect_stderr=False,
                  get_renderable=lambda: row(get_color("progress.bar_active"))):
            yield update
    except BaseException:
        console.print(row(_RED, failed=True), soft_wrap=True)
        raise
    else:
        console.print(row(_GREEN), soft_wrap=True)
