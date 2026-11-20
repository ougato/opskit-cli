"""Render optional, declarative progress for an approved host command."""

import re
from time import monotonic

from rich.console import Group
from rich.markup import escape
from rich.text import Text

from core.progress import _GREEN, _RED, _display_width, _global_step_width, _render_line, _render_pulse
from core.theme import get_color

class ToolProgress:
    def __init__(self, spec, console, output):
        def field(value):
            if (not isinstance(value, str) or not 1 <= len(value) <= 160
                    or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
                raise ValueError("invalid progress text")
            return value

        required = {"stages", "failed_label", "diagnostics", "success_codes"}
        if not isinstance(spec, dict) or set(spec) - {"quiet_prefixes", "compact_diagnostics"} not in (
            required, required | {"progress_marker", "progress_label"},
        ):
            raise ValueError("invalid progress description")
        stages, diagnostics, codes = (spec[key] for key in ("stages", "diagnostics", "success_codes"))
        if (not isinstance(stages, list) or not 1 <= len(stages) <= 16
                or not isinstance(diagnostics, list) or not 1 <= len(diagnostics) <= 24
                or not isinstance(codes, list) or not 1 <= len(codes) <= 8
                or any(type(code) is not int for code in codes)):
            raise ValueError("invalid progress description")
        self.stages = []
        for index, stage in enumerate(stages):
            if not isinstance(stage, list) or len(stage) != (1 if index == 0 else 2):
                raise ValueError("invalid progress stage")
            self.stages.append((field(stage[0]), field(stage[1]) if index else None))
        self.diagnostics = tuple(field(value).lower() for value in diagnostics)
        for key in ("quiet_prefixes", "compact_diagnostics"):
            values = spec.get(key, [])
            if not isinstance(values, list) or len(values) > 16:
                raise ValueError("invalid progress diagnostic rules")
            setattr(self, key, tuple(field(value) for value in values))
        self.seen_compact = set()
        self.compact_stack = False
        self.marker = None
        self.progress_label = None
        if "progress_marker" in spec:
            self.marker = re.compile(re.escape(field(spec["progress_marker"])) + r"([0-9]{1,3})%")
            self.progress_label = field(spec["progress_label"])
        self.failed_label = field(spec["failed_label"])
        self.codes = codes
        self.console = console
        self.output = output
        self.index = 0
        self.percent = None
        self.diagnostic = False
        self.live = None
        self.finished = False
        self.width = max(_global_step_width(), max(_display_width(label) + 2
                         for label in [self.progress_label or "", *(name for name, _ in self.stages)]))
        self.active_color = get_color("progress.bar_active")

    def __enter__(self):
        from rich.live import Live

        self.started = self.stage_started = monotonic()
        if self.console.is_terminal:
            self.live = Live(console=self.console, transient=True, refresh_per_second=10,
                             redirect_stdout=False, redirect_stderr=False, get_renderable=self.render)
            self.live.start(refresh=True)
        return self

    def __exit__(self, *exc):
        if exc[0] is not None and not self.finished:
            self._summary(False)
        if self.live is not None:
            self.live.stop()

    def render(self):
        label = self.stages[self.index][0]
        elapsed = monotonic() - self.stage_started
        stage = Text.from_markup(_render_pulse(escape(label), int(elapsed * 10), elapsed,
                                             self.active_color, self.width))
        if self.percent is None:
            return stage
        total = self._row(self.progress_label, self.percent, monotonic() - self.started, self.active_color)
        return Group(stage, total)

    def _row(self, label, percent, elapsed, color, *, status=""):
        return Text.from_markup(_render_line(escape(label), percent, elapsed, color, self.width,
                                            status=escape(status)))

    def _emit(self, row):
        if self.console.is_terminal:
            self.console.print(row, soft_wrap=True)
        else:
            self.output(row.plain)

    def line(self, line):
        if line.startswith(self.quiet_prefixes):
            return
        if self.compact_stack and (not line.strip() or line.startswith("    at ")):
            return
        self.compact_stack = False
        for marker in self.compact_diagnostics:
            _, match, details = line.partition(marker)
            if match:
                self.diagnostic = False
                self.compact_stack = True
                message = match + details
                if message not in self.seen_compact:
                    if len(self.seen_compact) < 128:
                        self.seen_compact.add(message)
                    self.output(message)
                return
        for index in range(self.index + 1, len(self.stages)):
            if self.stages[index][1] in line:
                self._summary(True)
                self.index = index
                self.stage_started = monotonic()
                break
        match = self.marker.search(line) if self.marker is not None else None
        if match and int(match[1]) <= 100:
            self.percent = int(match[1])
        normalized = line.lower()
        if any(marker in normalized for marker in self.diagnostics):
            self.diagnostic = True
            self.output(line)
        elif self.diagnostic and (not line.strip() or line[:1].isspace() or line.startswith("Caused by:")):
            self.output(line)
        else:
            self.diagnostic = False

    def _summary(self, success):
        label = self.stages[self.index][0]
        elapsed = monotonic() - self.stage_started
        self._emit(self._row(label, 100 if success else None, elapsed, _GREEN if success else _RED,
                             status="" if success else self.failed_label))

    def finish(self, code):
        self.finished = True
        success = code in self.codes
        self._summary(success)
        if self.progress_label is not None and (success or self.percent is not None):
            percent = 100 if success else self.percent
            self._emit(self._row(self.progress_label, percent, monotonic() - self.started,
                                 _GREEN if success else _RED))
