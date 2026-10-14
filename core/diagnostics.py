"""Read-only listener checks; an unrelated process must not pass service health."""
from pathlib import Path
import socket


def _files_worker(connection, expectations):
    import stat
    from network.worker import send
    checks = []
    try:
        for name, path, nonempty in expectations:
            try:
                info = Path(path).stat()
                healthy = stat.S_ISREG(info.st_mode) and (not nonempty or info.st_size > 0)
            except OSError:
                healthy = False
            checks.append({"name": name, "ok": healthy})
        send(connection, {"checks": checks})
    finally:
        connection.close()


def file_checks(expectations, *, timeout):
    """Bound filesystem metadata inspection without reading file contents."""
    from network.worker import run
    expectations = list(expectations)
    if len(expectations) > 128 or any(not isinstance(name, str) or len(name) > 128
                                     or type(nonempty) is not bool
                                     for name, _, nonempty in expectations):
        raise ValueError("invalid diagnostic file expectations")
    return run(_files_worker, (expectations,), timeout=timeout,
               label="diagnostic files")["checks"]


def _listener_worker(connection, expectations):
    from network.worker import send
    try:
        send(connection, {"checks": listener_checks(expectations)})
    finally:
        connection.close()


def listener_checks(expectations, *, timeout=None):
    """Check (protocol, port, executable) using local socket/process ownership.

    Missing visibility is a failed verification, rather than proof of a closed
    port. No connections, probes or privileged writes are performed.
    """
    import psutil
    expectations = list(expectations)
    for protocol, port, _ in expectations:
        if protocol not in {"tcp", "udp"} or not 1 <= port <= 65535:
            raise ValueError("invalid listener expectation")
    if not expectations:
        return []
    if timeout is not None:
        # psutil enumeration has no native timeout. This worker runs no commands.
        from network.worker import run
        return run(_listener_worker, (expectations,), timeout=timeout,
                   label="listener diagnostics")["checks"]
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return [{"name": f"{protocol}:{port}", "ok": False, "reason": "listeners_unavailable"}
                for protocol, port, _ in expectations]
    owners = {}
    listeners = {}
    for item in connections:
        if not item.laddr:
            continue
        if item.type == socket.SOCK_STREAM and item.status == psutil.CONN_LISTEN:
            protocol = "tcp"
        elif item.type == socket.SOCK_DGRAM:
            protocol = "udp"
        else:
            continue
        listeners.setdefault((protocol, item.laddr.port), []).append(item)
    checks = []
    for protocol, port, executable in expectations:
        candidates = listeners.get((protocol, port), [])
        matched = False
        for item in candidates:
            if item.pid is None:
                continue
            if item.pid not in owners:
                try:
                    owners[item.pid] = Path(psutil.Process(item.pid).exe()).resolve()
                except (psutil.Error, OSError):
                    owners[item.pid] = None
            if owners[item.pid] == Path(executable).resolve():
                matched = True
                break
        checks.append({"name": f"{protocol}:{port}", "ok": matched,
                       "reason": "verified" if matched else "listener_owner_not_verified"})
    return checks
