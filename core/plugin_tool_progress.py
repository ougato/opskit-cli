"""Render optional, declarative progress for an approved host command."""

import re
from time import monotonic


class ToolProgress:
    def __init__(self, spec, console, output):
        def field(value):
            if (not isinstance(value, str) or not 1 <= len(value) <= 160
                    or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
                raise ValueError("invalid progress text")
            return value

        if not isinstance(spec, dict) or set(spec) != {
            "stages", "progress_marker", "progress_label", "failed_label", "diagnostics", "success_codes",
        }:
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
        from rich.cells import cell_len
        from rich.text import Text

        label = self.stages[self.index][0]
        elapsed = monotonic() - self.stage_started
        position = int(elapsed * 10) % 32
        position = min(position, 32 - position)
        bar = "░" * position + "█" * 4 + "░" * (16 - position)
        result = Text(f"{label}{' ' * max(2, 14 - cell_len(label))}{bar}        {elapsed:.2f}s")
        if self.percent is not None:
            filled = self.percent // 5
            progress = "█" * filled + "░" * (20 - filled)
            result.append(f"\n{self.progress_label}{' ' * max(2, 14 - cell_len(self.progress_label))}"
                          f"{progress}  {self.percent:3d}%  {monotonic() - self.started:.2f}s")
        return result

    def line(self, line):
        for index in range(self.index + 1, len(self.stages)):
            if self.stages[index][1] in line:
                self._summary(True)
                self.index = index
                self.stage_started = monotonic()
                break
        match = self.marker.search(line)
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
        from rich.cells import cell_len

        label = self.stages[self.index][0]
        elapsed = monotonic() - self.stage_started
        progress = "100%" if success else self.failed_label
        bar = "█" * 20 if success else "░" * 20
        self.output(f"{label}{' ' * max(2, 14 - cell_len(label))}{bar}  {progress:>4}  {elapsed:.2f}s")

    def finish(self, code):
        self.finished = True
        success = code in self.codes
        self._summary(success)
        if success or self.percent is not None:
            percent = 100 if success else self.percent
            self.output(f"{self.progress_label}  {percent}%  {monotonic() - self.started:.2f}s")
