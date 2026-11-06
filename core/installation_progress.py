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
    def __init__(self, message):
        self.spinner = _ColorSpinner(message)
        self.live = None

    def start(self):
        if self.live is not None:
            return
        # The separator belongs to the transient display: stopping it removes
        # both lines, so a subsequent warning or error adds only its own gap.
        self.live = Live(Group(Text(""), self.spinner), console=console,
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
