"""One transient installation status, suspended around interactive questions."""
from contextlib import contextmanager

from rich.status import Status
from rich.text import Text

from core.i18n import t
from core.theme import console, get_color


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
        color = get_color("success")
        message = Text(t(key, branch=name), style=color, no_wrap=True, overflow="ellipsis")
        # Redirected output stays clean; final success/error remains available.
        if not console.is_terminal:
            return
        if self.status is None:
            console.print()
            self.status = Status(message, console=console, spinner="dots",
                                 spinner_style=color)
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
