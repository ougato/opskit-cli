"""One transient installation status, suspended around interactive questions."""
from contextlib import contextmanager
import time
from dataclasses import dataclass

from rich.console import Group
from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from core.i18n import t
from core.theme import console


class _ColorSpinner(Spinner):
    """Keep the spinner's color tied to its frame, not the refresh rate."""

    COLORS = ("red", "yellow", "green", "cyan", "blue", "magenta",
              "bright_red", "bright_yellow", "bright_green", "bright_cyan")

    def __init__(self, message):
        super().__init__("dots", text=message)

    def render(self, time):
        start = time if self.start_time is None else self.start_time
        frame = int((time - start) * self.speed / (self.interval / 1000.0)
                    + self.frame_no_offset) % len(self.frames)
        self.style = self.COLORS[frame % len(self.COLORS)]
        return super().render(time)


class _InstallationStatus:
    def __init__(self, message, output_console=None, *, separate=False):
        self.spinner = _ColorSpinner(message)
        self.separate = separate
        self.live = None
        self.output_console = output_console if output_console is not None else console

    def start(self):
        if self.live is not None:
            return
        renderable = Group(Text(""), self.spinner) if self.separate else self.spinner
        self.live = Live(renderable, console=self.output_console,
                         refresh_per_second=12.5, transient=True)
        self.live.start(refresh=True)

    def stop(self):
        if self.live is not None:
            self.live.stop()
            self.live = None

    def update(self, message):
        self.spinner.update(text=message)
        if self.live is not None:
            self.live.refresh()


@dataclass
class _WaitingStep:
    label: str
    failed_label: str | None
    started: float
    total: int | None
    completed: int = 0


class WaitingDisplay:
    """One host-owned, nestable transient wait indicator per invocation."""

    def __init__(self, output_console=None):
        self.console = output_console if output_console is not None else console
        self.messages = []
        self.status = None
        self.suspended = 0
        self.after_progress = False

    def start(self, message="", *, failed_label=None, total=None):
        if (not isinstance(message, str) or len(message) > 200
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in message)):
            raise ValueError("invalid waiting message")
        if len(self.messages) >= 8:
            raise ValueError("too many nested waiting scopes")
        if failed_label is not None and (not isinstance(failed_label, str) or not 1 <= len(failed_label) <= 40
                or not failed_label.isprintable()):
            raise ValueError("invalid progress failure label")
        if (failed_label is not None or total is not None) and (
                failed_label is None or type(total) is not int or total <= 0):
            raise ValueError("invalid progress total")
        self.messages.append(_WaitingStep(message, failed_label, time.monotonic(), total))
        self._refresh()

    def update(self, completed):
        step = self.messages[-1] if self.messages else None
        if (step is None or step.total is None or type(completed) is not int
                or not step.completed <= completed <= step.total):
            raise ValueError("invalid progress count")
        step.completed = completed

    def stop(self, *, failed=False):
        if not self.messages:
            raise ValueError("no active waiting scope")
        step = self.messages.pop()
        message, failed_label, started = step.label, step.failed_label, step.started
        failed = failed or (step.total is not None and step.completed != step.total)
        if failed_label is not None:
            if self.status is not None:
                self.status.stop()
                self.status = None
            from rich.markup import escape
            from core.progress import _GREEN, _RED, _display_width, _global_step_width, _render_line
            width = max(_global_step_width(), _display_width(message) + 2)
            percentage = min(99, step.completed * 100 // step.total) if failed else 100
            row = _render_line(escape(message), percentage, time.monotonic() - started,
                               _RED if failed else _GREEN, width, status=escape(failed_label) if failed else "")
            self.console.print(Text.from_markup(row), soft_wrap=True)
            self.after_progress = True
        self._refresh()

    def _refresh(self):
        from core.cli import non_interactive
        if not self.messages or self.suspended or non_interactive.get() or not self.console.is_terminal:
            if self.status is not None:
                self.status.stop()
                self.status = None
            return
        step = self.messages[-1]
        label, failed_label, started = step.label, step.failed_label, step.started
        if self.status is not None and (failed_label is not None or not isinstance(self.status, _InstallationStatus)):
            self.status.stop()
            self.status = None
        if failed_label is not None:
            from rich.live import Live
            from rich.markup import escape
            from core.progress import _display_width, _global_step_width, _render_line
            from core.theme import get_color
            width = max(_global_step_width(), _display_width(label) + 2)
            color = get_color("progress.bar_active")

            def render():
                elapsed = time.monotonic() - started
                percentage = min(99, step.completed * 100 // step.total)
                return Text.from_markup(_render_line(escape(label), percentage, elapsed, color, width))

            self.status = Live(console=self.console, transient=True, refresh_per_second=10,
                               redirect_stdout=False, redirect_stderr=False, get_renderable=render)
            self.status.start(refresh=True)
            return
        from core.prompt import menu_visible
        message = Text(label.strip() or t("prompt.waiting"), no_wrap=True, overflow="ellipsis")
        if self.status is None:
            self.status = _InstallationStatus(message, output_console=self.console,
                                               separate=self.after_progress or menu_visible())
            self.status.start()
        else:
            self.status.update(message)

    @contextmanager
    def suspend(self):
        self.suspended += 1
        self._refresh()
        try:
            yield
        finally:
            self.suspended -= 1
            self._refresh()

    @property
    def counting(self):
        return bool(self.messages and self.messages[-1].total is not None)

    @property
    def depth(self):
        return len(self.messages)

    def restore(self, depth):
        if self.depth > depth:
            del self.messages[depth:]
            self._refresh()

    def close(self):
        self.restore(0)


class InstallationProgress:
    def __init__(self):
        self.status = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.status is not None:
            self.status.stop()

    def __call__(self, phase, name=""):
        if phase not in {"fetch", "tools", "repair", "runtime",
                         "update_wait", "update_check", "update_fetch", "update_verify", "update_publish", "loading_inventory"}:
            return
        key = ("plugin.cloning_branch" if name else "plugin.cloning") if phase == "fetch" else "plugin_native.bootstrap." + phase
        if phase == "loading_inventory":
            key = "plugin.loading_inventory"
        message = Text(t(key, branch=name), no_wrap=True, overflow="ellipsis")
        # Redirected output stays clean; final success/error remains available.
        from core.cli import non_interactive
        if not console.is_terminal or non_interactive.get():
            return
        if self.status is None:
            from core.prompt import menu_visible
            self.status = _InstallationStatus(message, separate=menu_visible())
            self.status.start()
        else:
            self.status.update(message)

    @contextmanager
    def suspend(self):
        if self.status is not None:
            self.status.stop()
        try:
            yield
        finally:
            if self.status is not None:
                self.status.start()

    def interaction(self, callback):
        def ask(*args, **kwargs):
            with self.suspend():
                return callback(*args, **kwargs)
        return ask

    def copy_files(self, total):
        return self._counted("update_copy", "update_copy_failed", total)

    def download_bytes(self, total):
        return self._counted("update_download", "update_download_failed", total)

    @contextmanager
    def _counted(self, label, failure, total):
        from core.cli import non_interactive
        from core.sdk import progress_step
        if not console.is_terminal or non_interactive.get():
            yield None
            return
        with self.suspend():
            try:
                with progress_step(t("plugin_native.bootstrap." + label),
                                   t("plugin_native.bootstrap." + failure), total=total) as update:
                    yield update
            finally:
                if self.status is not None:
                    self.status.separate = True
