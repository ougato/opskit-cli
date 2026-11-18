"""Keep blocking OS volume enumeration outside the caller's process."""
import math

MAX_PARTITIONS = 256
FIELDS = {"mountpoint", "device", "fstype", "total", "used", "free", "percent"}


def _collect():
    import psutil
    result = []
    partitions = psutil.disk_partitions(all=False)
    if len(partitions) > MAX_PARTITIONS:
        raise ValueError("disk inventory exceeds quota")
    for part in partitions:
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue  # Preserve inaccessible-volume handling; not a timeout fallback.
        result.append(dict(mountpoint=part.mountpoint, device=part.device,
                           fstype=part.fstype, total=usage.total, used=usage.used,
                           free=usage.free, percent=usage.percent))
    return result


def _worker(connection):
    from network.worker import send
    try:
        try:
            message = {"ok": True, "data": _collect()}
            send(connection, message)
        except (OSError, ValueError, RuntimeError):
            send(connection, {"ok": False})
    finally:
        connection.close()


def sample(*, timeout=10, cancel_event=None):
    from network.worker import run
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid disk sampling timeout")
    message = run(_worker, (), timeout=timeout, label="disk sampling", cancel_event=cancel_event)
    rows = message.get("data")
    if message.get("ok") is not True or not isinstance(rows, list) or len(rows) > MAX_PARTITIONS:
        raise RuntimeError("disk sampling unavailable")
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise RuntimeError("invalid disk sampling result")
        if any(not isinstance(row[key], str) or len(row[key]) > 4096
               for key in ("mountpoint", "device", "fstype")):
            raise RuntimeError("invalid disk sampling result")
        if any(type(row[key]) is not int or row[key] < 0 for key in ("total", "used", "free")):
            raise RuntimeError("invalid disk sampling result")
        if (type(row["percent"]) not in (int, float) or not math.isfinite(row["percent"])
                or not 0 <= row["percent"] <= 100):
            raise RuntimeError("invalid disk sampling result")
    return rows
