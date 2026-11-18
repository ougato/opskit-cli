"""Keep source-mode Windows worker startup independent of the CLI UI."""
import sys
from importlib.machinery import ModuleSpec


def configure_main_spawn():
    """Tell multiprocessing that this CLI has no targets defined in __main__.

    All owned worker targets and their transferable types live in importable
    modules. Re-executing main.py in each spawned child would initialize the
    entire Typer application and change console settings for no benefit.
    Frozen executables retain their existing multiprocessing bootstrap.
    """
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        return
    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "__spec__", None) is None:
        # Python's spawn bootstrap deliberately leaves __main__ alone for this
        # module name, just as it does for package __main__ entry points.
        main.__spec__ = ModuleSpec("__main__", loader=None)
