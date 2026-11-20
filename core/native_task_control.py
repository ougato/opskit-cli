"""Host task cancellation; no worker or plugin owns the console input."""
import sys
import time


def periodic_checkpoint(checkpoint, *, interval=0.1):
    """Poll an expensive check by elapsed time within one host operation."""
    next_check = 0.0

    def poll():
        nonlocal next_check
        if time.monotonic() >= next_check:
            checkpoint()
            next_check = time.monotonic() + interval

    return poll


def task_checkpoint(checkpoint, *, output=None):
    """Poll Windows ESC without blocking, and preserve Ctrl+C rollback semantics."""
    from core.cli import non_interactive
    from core.plugin_capabilities import _interactive_input
    if non_interactive.get() or not _interactive_input():
        return checkpoint
    from core.i18n import t
    if output is None:
        from core.prompt import console
        output = lambda line: console.print(line, markup=False, highlight=False)
    def check():
        checkpoint()
        if sys.platform == "win32":
            import msvcrt
            for _ in range(16):
                if not msvcrt.kbhit():
                    break
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    continue
                if key in ("\x1b", "\x03"):
                    output(t("plugin_native.task.cancelled"))
                    # This must escape broker dispatch, not become a reply the
                    # plugin can catch before committing partial state.
                    raise KeyboardInterrupt
    return check
