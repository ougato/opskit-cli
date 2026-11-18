"""HTTP lookup/measurement bounded by a parent-owned wall-clock deadline."""
import ipaddress
import time
from urllib.parse import urlsplit

from network.worker import run, send

MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024


def _worker(connection, urls, measurement):
    import httpx
    total = 0
    last_progress = 0
    try:
        with httpx.Client(timeout=3, follow_redirects=measurement, max_redirects=5,
                          headers={"Accept-Encoding": "identity"}) as client:
            for url in urls:
                try:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        if measurement:
                            for chunk in response.iter_raw():
                                total += min(len(chunk), MAX_DOWNLOAD_BYTES - total)
                                now = time.monotonic()
                                if now - last_progress >= .1:
                                    send(connection, {"progress": total})
                                    last_progress = now
                                if total >= MAX_DOWNLOAD_BYTES:
                                    send(connection, {"ok": True, "data": total, "limit_reached": True})
                                    return
                            send(connection, {"ok": True, "data": total})
                            return
                        if response.headers.get("content-encoding", "identity") != "identity":
                            continue
                        body = bytearray()
                        for chunk in response.iter_raw():
                            if len(body) + len(chunk) > 1024:
                                raise ValueError("IP response exceeds limit")
                            body.extend(chunk)
                        value = str(ipaddress.ip_address(body.decode("ascii").strip()))
                        send(connection, {"ok": True, "data": value})
                        return
                except (httpx.HTTPError, ValueError):
                    if measurement:
                        break
        send(connection, {"ok": False})
    finally:
        connection.close()


def public_ip(urls, timeout, cancel_event=None):
    message = run(_worker, (urls, False), timeout=timeout, label="HTTP", cancel_event=cancel_event)
    return message.get("data") if message.get("ok") else None


def download(url, duration, cancel_event=None):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must be HTTP(S) without credentials")
    started = time.monotonic()

    def complete_window(latest):
        if not isinstance(latest, int) or latest <= 0:
            raise TimeoutError("HTTP measurement deadline exceeded without data")
        return {"ok": True, "data": latest}

    message = run(_worker, ([url], True), timeout=duration, label="HTTP",
                  cancel_event=cancel_event, on_deadline=complete_window,
                  on_cancel=(lambda latest: {"ok": True, "data": latest or 0, "cancelled": True})
                  if cancel_event is not None else None)
    if not message.get("ok"):
        raise RuntimeError("HTTP measurement failed")
    if message["data"] <= 0 and not message.get("cancelled"):
        raise RuntimeError("HTTP measurement returned no data")
    elapsed = time.monotonic() - started
    return {"bytes": message["data"], "elapsed_seconds": elapsed,
            "bytes_per_second": message["data"] / elapsed if elapsed else 0,
            "limit_reached": message.get("limit_reached", False), "cancelled": message.get("cancelled", False)}
