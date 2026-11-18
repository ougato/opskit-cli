"""TCP scanning with bounded sockets, one DNS lookup and a total deadline."""
import errno
import ipaddress
import math
import selectors
import socket
import time


def scan(host, ports, *, timeout, concurrency, total_timeout):
    from network.commands import PortResult
    if not ports:
        return []
    if (not 1 <= concurrency <= 50 or not math.isfinite(timeout) or timeout <= 0
            or not math.isfinite(total_timeout) or total_timeout <= 0
            or any(not 1 <= port <= 65535 for port in ports)):
        raise ValueError("invalid ports, timeout or concurrency")
    deadline = time.monotonic() + total_timeout

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("port scan total deadline exceeded")
        return budget

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        from network.resolver import resolve
        addresses = resolve(host, timeout=remaining())
        if not addresses:
            raise RuntimeError("port scan DNS lookup failed")
        address = ipaddress.ip_address(addresses[0])
    remaining()
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    results = []
    pending = {}
    work = iter(ports)
    exhausted = False
    in_progress = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                   getattr(errno, "WSAEWOULDBLOCK", 10035), getattr(errno, "WSAEINPROGRESS", 10036)}
    with selectors.DefaultSelector() as selector:
        def finish(connection, open_):
            port, started = pending.pop(connection)
            try:
                selector.unregister(connection)
            except KeyError:
                pass
            connection.close()
            results.append(PortResult(host, port, "tcp", open_, (time.monotonic() - started) * 1000))

        try:
            while pending or not exhausted:
                remaining()
                while not exhausted and len(pending) < concurrency:
                    remaining()
                    port = next(work, None)
                    if port is None:
                        exhausted = True
                        break
                    connection = socket.socket(family, socket.SOCK_STREAM)
                    pending[connection] = (port, time.monotonic())
                    connection.setblocking(False)
                    endpoint = (str(address), port, 0, 0) if family == socket.AF_INET6 else (str(address), port)
                    result = connection.connect_ex(endpoint)
                    if result == 0:
                        finish(connection, True)
                    elif result in in_progress:
                        selector.register(connection, selectors.EVENT_WRITE)
                    else:
                        finish(connection, False)
                if not pending:
                    continue
                next_expiry = min(started + timeout for _, started in pending.values())
                wait = min(remaining(), max(0, next_expiry - time.monotonic()))
                events = selector.select(wait)
                remaining()
                for key, _ in events:
                    finish(key.fileobj, key.fileobj.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0)
                now = time.monotonic()
                for connection, (_, started) in list(pending.items()):
                    if now - started >= timeout:
                        finish(connection, False)
        finally:
            for connection in pending:
                connection.close()
    results.sort(key=lambda item: item.port)
    return results
