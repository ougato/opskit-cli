"""Container-only authenticated HTTP/CONNECT proxy with DNS rebinding checks."""
from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import os
import selectors
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit


def allowed_addresses(host: str, port: int, private_targets: list[str]) -> list[tuple]:
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
            port = parsed.port or (443 if method == "CONNECT" else 80)
            if not host or parsed.username or parsed.password or method != "CONNECT" and parsed.scheme != "http":
                raise PermissionError("invalid proxy destination")
            addresses = allowed_addresses(host, port, json.loads(os.environ.get("OPSKIT_PRIVATE_TARGETS", "[]")))
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
