"""Bounded HTTPS broker: pinned DNS, verified TLS, no redirects or ambient auth."""
import base64
import http.client
import socket
import ssl
import time
from urllib.parse import urlsplit

MAX_BODY = 1024 * 1024
MAX_ENCODED_BODY = 4 * ((MAX_BODY + 2) // 3)


def valid_etag(value):
    return (isinstance(value, str) and 2 < len(value) <= 256
            and value.startswith('"') and value.endswith('"')
            and all(33 <= ord(char) <= 126 and char != '"' for char in value[1:-1]))


def _validate(url, method, headers, body):
    if (not isinstance(url, str) or len(url) > 8192 or "\\" in url
            or any(ord(c) < 33 or ord(c) > 126 for c in url)):
        raise ValueError("invalid HTTPS URL")
    target = urlsplit(url)
    if (target.scheme != "https" or not target.hostname or target.username is not None
            or target.password is not None or target.fragment or target.port not in (None, 443)):
        raise ValueError("HTTPS port 443 required")
    from core.plugin_egress import fetch_target
    authority = "[" + target.hostname + "]:443" if ":" in target.hostname else target.hostname + ":443"
    fetch_target(authority)
    if method not in {"GET", "POST", "PUT", "DELETE", "HEAD"}:
        raise ValueError("unsupported HTTP method")
    if not isinstance(headers, dict) or len(headers) > 16:
        raise ValueError("invalid HTTP headers")
    allowed = {"accept", "content-type", "authorization", "if-match", "if-none-match"}
    for key, value in headers.items():
        if (not isinstance(key, str) or key.lower() not in allowed or not isinstance(value, str)
                or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise ValueError("invalid HTTP header")
    if len({key.lower() for key in headers}) != len(headers):
        raise ValueError("duplicate HTTP header")
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise ValueError("HTTP request body exceeds limit")
    return target


def _perform(url, method, headers, body, private_targets):
    target = _validate(url, method, headers, body)
    from core.plugin_egress import allowed_addresses
    addresses = allowed_addresses(target.hostname, 443, private_targets)
    raw = None
    connection = http.client.HTTPSConnection(target.hostname, 443, timeout=10)
    try:
        # Connect only the checked numeric address, preserving TLS SNI and Host.
        address = addresses[0]
        raw = socket.socket(address[0], address[1], address[2])
        raw.settimeout(10)
        raw.connect(address[4])
        connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=target.hostname)
        raw = None
        connection.request(method, (target.path or "/") + ("?" + target.query if target.query else ""),
                           body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError("HTTP response body exceeds limit")
        etag = response.getheader("ETag")
        return {"status": response.status, "body": base64.b64encode(data).decode("ascii"),
                "etag": etag if valid_etag(etag) else None}
    finally:
        connection.close()
        if raw is not None:
            raw.close()


def _worker(connection, url, method, headers, body, private_targets):
    from network.worker import send
    try:
        send(connection, {"value": _perform(url, method, headers, body, private_targets)})
    except Exception:
        # URLs, headers and TLS errors can contain secrets. Never echo them.
        send(connection, {"error": True})
    finally:
        connection.close()


def request(url, method, headers, body, *, private_targets, deadline, checkpoint):
    _validate(url, method, headers, body)
    from network.worker import owned_process
    checkpoint()
    with owned_process(_worker, (url, method, headers, body, private_targets), label="plugin HTTPS") as (receive, process):
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise TimeoutError("plugin HTTPS deadline exceeded")
            if receive.poll(0.05):
                import json
                value = json.loads(receive.recv_bytes(MAX_ENCODED_BODY + 1024))
                if not isinstance(value, dict) or set(value) != {"value"}:
                    raise RuntimeError("plugin HTTPS request failed")
                result = value["value"]
                if (not isinstance(result, dict) or set(result) != {"status", "body", "etag"}
                        or (result["etag"] is not None and not valid_etag(result["etag"]))
                        or type(result["status"]) is not int or not 100 <= result["status"] <= 599
                        or not isinstance(result["body"], str)
                        or len(base64.b64decode(result["body"], validate=True)) > MAX_BODY):
                    raise ValueError("invalid HTTPS worker response")
                checkpoint()
                return result
            if not process.is_alive():
                raise RuntimeError("plugin HTTPS worker exited")
