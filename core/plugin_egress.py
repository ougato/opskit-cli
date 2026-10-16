"""Container-only authenticated HTTP/CONNECT proxy with DNS rebinding checks."""
from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import os
import re
import selectors
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit


def fetch_target(value: str) -> tuple[str, int]:
    """Canonical single TCP destination, without URL credentials or paths."""
    if (not isinstance(value, str) or not value or not value.isascii()
            or any(character.isspace() or ord(character) < 32 for character in value)
            or "\\" in value or "%" in value):
        raise ValueError("invalid fetch destination")
    parsed = urlsplit("//" + value)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or parsed.port is None):
        raise ValueError("invalid fetch destination")
    host, port = parsed.hostname.lower(), parsed.port
    if not 1 <= port <= 65535:
        raise ValueError("invalid fetch destination")
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        host = host.rstrip(".")
        if (not host or len(host) > 253 or ":" in host
                or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                       for label in host.split("."))):
            raise ValueError("invalid fetch destination") from None
    return host, port


def allowed_addresses(host: str, port: int, private_targets: list[str], *,
                      target: tuple[str, int] | None = None) -> list[tuple]:
    if target is not None:
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        if fetch_target(authority) != target:
            # Reject before DNS as well as connect: a denied host must not be
            # usable as a DNS side channel. A private grant cannot widen target.
            raise PermissionError("destination outside fetch scope")
        host = target[0]
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    explicitly_allowed = f"{host.lower()}:{port}" in private_targets
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses) and not explicitly_allowed:
        raise PermissionError("private or reserved destination denied")
    # Connect using resolved addresses, never resolve the hostname again.
    return addresses


class Proxy(socketserver.BaseRequestHandler):
    def handle(self):
        header = bytearray()
        try:
            header_deadline = time.monotonic() + self.server.header_timeout
            while b"\r\n\r\n" not in header:
                remaining = header_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("proxy header deadline exceeded")
                self.request.settimeout(remaining)
                byte = self.request.recv(1)
                if not byte:
                    return
                header.extend(byte)
                if len(header) > 16384:
                    raise ValueError("proxy header too large")
            self.request.settimeout(15)
            first, *lines = bytes(header).split(b"\r\n")
            method, target, version = first.decode("ascii").split(" ", 2)
            auth = next((line.split(b":", 1)[1].strip() for line in lines if line.lower().startswith(b"proxy-authorization:")), b"")
            expected = b"Basic " + base64.b64encode(("opskit:" + os.environ["OPSKIT_PROXY_TOKEN"]).encode())
            if not hmac.compare_digest(auth, expected):
                raise PermissionError("proxy authentication required")
            parsed = urlsplit("//" + target if method == "CONNECT" else target)
            host = parsed.hostname
            port = parsed.port if parsed.port is not None else (443 if method == "CONNECT" else 80)
            if not 1 <= port <= 65535:
                raise PermissionError("invalid proxy destination port")
            if not host or parsed.username or parsed.password or method != "CONNECT" and parsed.scheme != "http":
                raise PermissionError("invalid proxy destination")
            if self.server.fetch_target is not None and method != "CONNECT":
                if method not in ("GET", "POST") or version not in ("HTTP/1.0", "HTTP/1.1"):
                    raise PermissionError("invalid fetch request")
                hosts = [line.split(b":", 1)[1].strip().decode("ascii") for line in lines
                         if line.lower().startswith(b"host:")]
                if len(hosts) != 1:
                    raise PermissionError("invalid fetch Host header")
                header = urlsplit("//" + hosts[0])
                header_port = header.port if header.port is not None else 80
                header_authority = (f"[{header.hostname}]:{header_port}" if header.hostname and ":" in header.hostname
                                    else f"{header.hostname}:{header_port}")
                if (header.username is not None or header.password is not None
                        or header.path or header.query or header.fragment
                        or fetch_target(header_authority) != self.server.fetch_target):
                    raise PermissionError("fetch Host differs from destination")
            addresses = allowed_addresses(host, port, json.loads(os.environ.get("OPSKIT_PRIVATE_TARGETS", "[]")),
                                          target=self.server.fetch_target)
            upstream = None
            for family, kind, protocol, _, address in addresses:
                candidate = socket.socket(family, kind, protocol)
                candidate.settimeout(10)
                try:
                    candidate.connect(address)
                    upstream = candidate
                    break
                except OSError:
                    candidate.close()
            if upstream is None:
                raise OSError("destination unavailable")
            with upstream:
                if method == "CONNECT":
                    self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                else:
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    clean = [line for line in lines if not line.lower().startswith((b"proxy-authorization:", b"proxy-connection:"))]
                    upstream.sendall(f"{method} {path} {version}\r\n".encode() + b"\r\n".join(clean))
                with selectors.DefaultSelector() as poller:
                    poller.register(self.request, selectors.EVENT_READ, upstream)
                    poller.register(upstream, selectors.EVENT_READ, self.request)
                    deadline = time.monotonic() + 300
                    while time.monotonic() < deadline:
                        ready = poller.select(1)
                        for key, _ in ready:
                            data = key.fileobj.recv(65536)
                            if not data:
                                return
                            key.data.sendall(data)
        except (OSError, ValueError, PermissionError):
            try:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16
    header_timeout = 15
    max_connections = 16

    def __init__(self, *args, **kwargs):
        value = os.environ.get("OPSKIT_FETCH_TARGET")
        self.fetch_target = fetch_target(value) if value is not None else None
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


if __name__ == "__main__":
    with Server(("0.0.0.0", 8080), Proxy) as server:
        server.serve_forever()
