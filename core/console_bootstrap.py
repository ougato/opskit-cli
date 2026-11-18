"""Configure command-line output without replacing borrowed streams."""
from __future__ import annotations

import os
import sys


def configure_standard_streams() -> None:
    """Use UTF-8 where supported, respecting an explicit encoding override.

    Embedded hosts and tests may provide streams without an encoding, a buffer,
    or a reconfigure method.  They remain owned by their caller.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            # Closed, already-read or otherwise non-reconfigurable streams are
            # still usable by their owner; do not wrap or close their buffer.
            continue
