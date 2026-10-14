"""Exercise proxy admission and slow-client deadlines using real sockets."""
import socket
import socketserver
import threading
import time

from core.plugin_egress import Proxy, Server


def test_connection_limit_and_slot_reuse():
    entered = threading.Event()
    release = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            entered.set()
            release.wait(3)

    class SingleSlot(Server):
        max_connections = 1

    with SingleSlot(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2):
                assert entered.wait(2)
                with socket.create_connection(server.server_address, timeout=2) as excess:
                    assert excess.recv(1) == b""
                release.set()
            # Acquiring the semaphore proves the handler returned its permit.
            assert server._slots.acquire(timeout=2)
            server._slots.release()
        finally:
            release.set()
            server.shutdown()
            thread.join(2)


def test_slow_header_has_total_deadline():
    class ShortDeadline(Server):
        header_timeout = 0.2

    with ShortDeadline(("127.0.0.1", 0), Proxy) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                started = time.monotonic()
                client.sendall(b"C")
                assert b"403" in client.recv(4096)
                assert time.monotonic() - started < 1.5
            assert server._slots.acquire(timeout=2)
            server._slots.release()
        finally:
            server.shutdown()
            thread.join(2)
