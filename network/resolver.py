"""System DNS in a disposable process: a blocked resolver must be cancellable."""
import json
import socket


def _worker(connection, host, family, reverse):
    try:
        if reverse:
            data = socket.gethostbyaddr(host)[0]
        else:
            data = sorted({item[4][0] for item in socket.getaddrinfo(host, None, family=family)})
        message = {"ok": True, "data": data}
    except (OSError, ValueError):
        message = {"ok": False}
    try:
        encoded = json.dumps(message).encode("utf-8")
        if len(encoded) > 65536:
            encoded = b'{"ok": false}'
        connection.send_bytes(encoded)
    finally:
        connection.close()


def resolve(host, *, family=socket.AF_UNSPEC, reverse=False, timeout=10):
    from network.worker import run
    message = run(_worker, (host, family, reverse), timeout=timeout, label="DNS")
    return message.get("data") if message.get("ok") else None
