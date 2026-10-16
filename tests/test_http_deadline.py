"""Real local HTTP responses exercise total deadlines and process cleanup."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
import multiprocessing
import os
import json
import threading
import time

import pytest

from network import http_measure


@pytest.fixture
def http_server():
    stop = threading.Event()
    started = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            started.set()
            self.send_response(200)
            self.send_header("Content-Length", "1000000" if self.path != "/ip" else "9")
            self.end_headers()
            try:
                if self.path == "/ip":
                    self.wfile.write(b"127.0.0.1")
                elif self.path == "/large":
                    self.wfile.write(b"x" * 4096)
                    self.wfile.flush()
                    stop.wait(5)
                elif self.path == "/trickle":
                    while not stop.wait(.05):
                        self.wfile.write(b"x" * 1024)
                        self.wfile.flush()
                elif self.path == "/slow-ip":
                    # Stay below both the body cap and the 3-second read timeout;
                    # only the parent's total deadline can end this response.
                    while not stop.wait(.25):
                        self.wfile.write(b"1")
                        self.wfile.flush()
                else:
                    stop.wait(15)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", started
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cold_http_lookup_includes_spawn_in_total_deadline_without_worker_residue(http_server):
    url, _ = http_server
    before = {child.pid for child in multiprocessing.active_children()}
    begin = time.monotonic()
    with pytest.raises(TimeoutError):
        # Cold spawn/import consumes the same budget and may exhaust it before
        # the request begins. Real trickled I/O is verified separately below.
        http_measure.public_ip([url + "/slow-ip"], 5)
    assert time.monotonic() - begin < 7
    assert {child.pid for child in multiprocessing.active_children()} == before


def _ready_http_worker(connection, imported, begin, lifetime, parent_pid, urls, measurement):
    import httpx  # Complete cold imports before the I/O-specific observation.
    assert callable(httpx.Client)
    imported.set()
    from network import worker
    worker._owned_target(connection, lifetime, begin, parent_pid, http_measure._worker, (urls, measurement))


@contextmanager
def prepared_http_context(monkeypatch, urls, measurement=False):
    from network import worker
    context = multiprocessing.get_context("spawn")
    receive, transmit = context.Pipe(duplex=False)
    lifetime_read, lifetime_write = context.Pipe(duplex=False)
    ready = context.Event()
    begin, release = context.Pipe(duplex=False)
    process = context.Process(target=_ready_http_worker,
                              args=(transmit, ready, begin, lifetime_read, os.getpid(), urls, measurement), daemon=True)
    process.start()
    try:
        assert ready.wait(30), "fixture could not prepare a real HTTP worker"
        # Supply a real, already imported process to the unchanged supervisor.
        # The production supervisor releases its pipe gate after attaching the
        # Windows Job; clocks, request I/O and deadlines remain real.
        class PreparedContext:
            pipes = 0

            def Pipe(self, duplex):
                assert duplex is False
                self.pipes += 1
                assert 1 <= self.pipes <= 3
                return ((receive, transmit), (lifetime_read, lifetime_write), (begin, release))[self.pipes - 1]

            def Process(self, target, args, daemon):
                assert target is worker._owned_target and daemon
                assert args == (transmit, lifetime_read, begin, os.getpid(), http_measure._worker, (urls, measurement))
                monkeypatch.setattr(process, "start", lambda: None)
                return process

        monkeypatch.setattr(worker.multiprocessing, "get_context", lambda method: PreparedContext())
        yield
    finally:
        receive.close()
        transmit.close()
        lifetime_read.close()
        lifetime_write.close()
        begin.close()
        release.close()
        try:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()
        except ValueError:
            pass  # The real supervisor already closed the process handle.


def test_incomplete_trickled_io_hits_total_deadline_without_worker_residue(http_server, monkeypatch):
    url, started = http_server
    before = {child.pid for child in multiprocessing.active_children()}
    urls = [url + "/slow-ip"]
    with prepared_http_context(monkeypatch, urls):
        observed = time.monotonic()
        with pytest.raises(TimeoutError):
            http_measure.public_ip(urls, 5)
        assert started.is_set()
        assert time.monotonic() - observed < 7
        assert {child.pid for child in multiprocessing.active_children()} == before


def test_oversized_ip_response_falls_back_without_reading_full_body(http_server):
    url, _ = http_server
    before = {child.pid for child in multiprocessing.active_children()}
    # This verifies bounded body consumption and fallback, not cold-spawn
    # latency. Use the CLI's ordinary budget; exact consumption is checked
    # below without depending on scheduling or socket buffering.
    assert http_measure.public_ip([url + "/large", url + "/ip"], 10) == "127.0.0.1"
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.parametrize("chunk_size", [1025, 4096])
def test_oversized_ip_stream_is_closed_before_another_body_chunk(monkeypatch, chunk_size):
    import httpx
    messages, requests, closed = [], [], []

    class Oversized(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * chunk_size
            pytest.fail("worker read more body after its 1024-byte quota")

        def close(self):
            closed.append(True)

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/large":
            return httpx.Response(200, stream=Oversized())
        return httpx.Response(200, stream=httpx.ByteStream(b"127.0.0.1"))

    client = httpx.Client
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=transport, **kwargs))

    class Connection:
        def send_bytes(self, message):
            messages.append(json.loads(message))

        def close(self):
            pass

    http_measure._worker(Connection(), ["https://example.invalid/large", "https://example.invalid/ip"], False)
    assert requests == ["/large", "/ip"]
    assert closed == [True]
    assert messages == [{"ok": True, "data": "127.0.0.1"}]


def test_trickled_download_finishes_window_and_reclaims_worker(http_server, monkeypatch):
    url, started = http_server
    before = {child.pid for child in multiprocessing.active_children()}
    # This case proves progress during real I/O. Cold startup accounting is
    # separately checked without requiring data to arrive before the deadline.
    with prepared_http_context(monkeypatch, [url + "/trickle"], measurement=True):
        result = http_measure.download(url + "/trickle", 5)
        assert started.is_set()
        assert result["bytes"] > 0 and result["bytes_per_second"] > 0
        assert 4.9 <= result["elapsed_seconds"] < 7
        assert {child.pid for child in multiprocessing.active_children()} == before


def test_empty_stalled_download_is_failure(http_server):
    url, _ = http_server
    with pytest.raises(TimeoutError, match="without data"):
        http_measure.download(url + "/stall", 2.5)


def test_cancel_after_request_begins_reclaims_worker(http_server, monkeypatch):
    url, started = http_server
    cancellation = threading.Event()
    before = {child.pid for child in multiprocessing.active_children()}

    def cancel():
        if started.wait(5):
            cancellation.set()

    thread = threading.Thread(target=cancel, daemon=True)
    urls = [url + "/stall"]
    with prepared_http_context(monkeypatch, urls):
        thread.start()
        try:
            with pytest.raises(InterruptedError):
                http_measure.public_ip(urls, 8, cancellation)
            assert started.is_set()
            assert {child.pid for child in multiprocessing.active_children()} == before
        finally:
            thread.join(timeout=6)


def test_download_cap_counts_only_allowed_bytes(monkeypatch):
    import httpx
    messages = []
    client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 4096)))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=transport, **kwargs))
    monkeypatch.setattr(http_measure, "MAX_DOWNLOAD_BYTES", 1024)

    class Connection:
        def send_bytes(self, message):
            messages.append(json.loads(message))

        def close(self):
            pass

    http_measure._worker(Connection(), ["https://example.invalid"], True)
    assert messages[-1] == {"ok": True, "data": 1024, "limit_reached": True}
    assert all(message.get("progress", 0) <= 1024 for message in messages)
