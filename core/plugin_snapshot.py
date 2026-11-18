"""Disposable host worker for potentially blocking plugin snapshot I/O."""
from pathlib import Path


def _copy_worker(connection, source, target, limit, skip_dirs):
    from core.plugin_sandbox import _copy_tree
    from network.worker import send
    try:
        try:
            _copy_tree(Path(source), Path(target), limit, skip_dirs=frozenset(skip_dirs))
        except (OSError, ValueError, RuntimeError):
            send(connection, {"ok": False})
        else:
            send(connection, {"ok": True})
    finally:
        connection.close()


def copy_snapshot(source, target, limit, *, timeout, skip_dirs):
    from network.worker import run
    result = run(_copy_worker, (str(source), str(target), limit, sorted(skip_dirs)),
                 timeout=timeout, label="plugin snapshot")
    if result != {"ok": True}:
        raise RuntimeError("plugin snapshot copy failed")


def _copy_pair_worker(connection, items):
    from core.plugin_sandbox import _copy_tree
    from network.worker import send
    try:
        try:
            for source, target, limit, skip_dirs in items:
                _copy_tree(Path(source), Path(target), limit, skip_dirs=frozenset(skip_dirs))
        except (OSError, ValueError, RuntimeError):
            send(connection, {"ok": False})
        else:
            send(connection, {"ok": True})
    finally:
        connection.close()


def copy_snapshot_pair(items, *, timeout, cancel_event=None):
    """Copy independent trees under one disposable, deadline-bound worker."""
    from network.worker import run
    serialized = [(str(source), str(target), limit, sorted(skip_dirs))
                  for source, target, limit, skip_dirs in items]
    if len(serialized) != 2:
        raise ValueError("exactly two snapshot trees are required")
    result = run(_copy_pair_worker, (serialized,), timeout=timeout, label="plugin snapshots",
                 cancel_event=cancel_event)
    if result != {"ok": True}:
        raise RuntimeError("plugin snapshot copy failed")
