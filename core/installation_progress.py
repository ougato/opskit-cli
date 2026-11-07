"""One transient installation status, suspended around interactive questions."""
from contextlib import contextmanager

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
    def __init__(self, message, output_console=None):
        self.spinner = _ColorSpinner(message)
        self.live = None
        self.output_console = output_console if output_console is not None else console

    def start(self):
        if self.live is not None:
            return
        # The separator belongs to the transient display: stopping it removes
        # both lines, so a subsequent warning or error adds only its own gap.
        self.live = Live(Group(Text(""), self.spinner), console=self.output_console,
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


class WaitingDisplay:
    """One host-owned, nestable transient wait indicator per invocation."""

    def __init__(self, output_console=None):
        self.console = output_console if output_console is not None else console
        self.messages = []
        self.status = None
        self.suspended = 0

    def start(self, message=""):
        if (not isinstance(message, str) or len(message) > 200
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in message)):
            raise ValueError("invalid waiting message")
        if len(self.messages) >= 8:
            raise ValueError("too many nested waiting scopes")
        self.messages.append(message)
        self._refresh()

    def stop(self):
        if not self.messages:
            raise ValueError("no active waiting scope")
        self.messages.pop()
        self._refresh()

    def _refresh(self):
        from core.cli import non_interactive
        if not self.messages or self.suspended or non_interactive.get() or not self.console.is_terminal:
            if self.status is not None:
                self.status.stop()
                self.status = None
            return
        message = Text(self.messages[-1], no_wrap=True, overflow="ellipsis")
        if self.status is None:
            self.status = _InstallationStatus(message, output_console=self.console)
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

    def close(self):
        self.messages.clear()
        self._refresh()


class InstallationProgress:
    def __init__(self):
        self.status = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.status is not None:
            self.status.stop()

    def __call__(self, phase, name=""):
        if phase not in {"fetch", "tools", "repair", "runtime"}:
            return
        key = ("plugin.cloning_branch" if name else "plugin.cloning") if phase == "fetch" else "plugin_native.bootstrap." + phase
        message = Text(t(key, branch=name), no_wrap=True, overflow="ellipsis")
        # Redirected output stays clean; final success/error remains available.
        if not console.is_terminal:
            return
        if self.status is None:
            self.status = _InstallationStatus(message)
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
