"""Read X-UI service logs with the same redaction for both interfaces."""
from contextlib import closing
import subprocess

from core.i18n import t
from core.secrets import redact
from xui.constants import JOURNALCTL_COMMAND, JOURNAL_NO_PAGER_ARG, XUI_SERVICE


def service_logs(lines: int | str, *, follow: bool = False):
    from core.process import stream

    command = [JOURNALCTL_COMMAND, "-u", XUI_SERVICE, "-n", str(lines), JOURNAL_NO_PAGER_ARG]
    if follow:
        command.append("-f")
    try:
        with closing(stream(command, timeout=None if follow else 30)) as iterator:
            while True:
                try:
                    line = next(iterator)
                except StopIteration as result:
                    if result.value:
                        raise RuntimeError(t("xui.error.journal_failed")) from None
                    return
                yield redact(line)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(t("xui.error.journal_failed")) from exc
