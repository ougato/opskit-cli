"""Fetch target checks precede DNS and retain private destination protection."""
import base64
from contextlib import contextmanager
import http.server
import socket
import threading

import pytest

from core import plugin_egress as egress


@pytest.mark.parametrize('authority,expected', [
    ('Git.Example.:443', ('git.example', 443)),
    ('127.0.0.1:8443', ('127.0.0.1', 8443)),
    ('[2001:4860:4860:0::8888]:22', ('2001:4860:4860::8888', 22)),
])
def test_canonical_fetch_target(authority, expected):
    assert egress.fetch_target(authority) == expected


@pytest.mark.parametrize('authority', ['', 'git.example', 'https://git.example:443',
    'user@git.example:443', 'git.example:443/path', 'git.example:443?token=value',
    'git.example:443#fragment', 'git.example:0', 'git.example:65536',
    'git.example:443\n', 'bad_host:443', '[fe80::1%eth0]:22', '汉字.example:443'])
def test_invalid_fetch_target_refused(authority):
    with pytest.raises(ValueError):
        egress.fetch_target(authority)


@pytest.mark.parametrize('host,port', [('other.example', 443), ('git.example', 22)])
def test_target_mismatch_denied_before_dns(monkeypatch, host, port):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('denied host resolved'))
    with pytest.raises(PermissionError, match='fetch scope'):
        egress.allowed_addresses(host, port, [f'{host}:{port}'], target=('git.example', 443))


def test_bound_target_does_not_grant_private_addresses(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(PermissionError, match='private'):
        egress.allowed_addresses('git.example', 443, [], target=('git.example', 443))


def test_empty_fetch_environment_fails_closed(monkeypatch):
    monkeypatch.setenv('OPSKIT_FETCH_TARGET', '')
    with pytest.raises(ValueError):
        egress.Server(('127.0.0.1', 0), egress.Proxy)


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def read_headers(client):
    response = bytearray()
    while b'\r\n\r\n' not in response:
        chunk = client.recv(4096)
        assert chunk and len(response) + len(chunk) <= 16384
        response.extend(chunk)
    return bytes(response)


@pytest.mark.parametrize('mode', ['connect', 'http', 'wrong-port', 'wrong-host-header', 'duplicate-host'])
def test_real_bound_proxy(monkeypatch, mode):
    import json
    requests = []
    class Peer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
        def log_message(self, *args):
            pass
    with serving(http.server.ThreadingHTTPServer(('127.0.0.1', 0), Peer)) as (_, peer_port):
        target = f'127.0.0.1:{peer_port}'
        monkeypatch.setenv('OPSKIT_FETCH_TARGET', target)
        monkeypatch.setenv('OPSKIT_PRIVATE_TARGETS', json.dumps([target]))
        monkeypatch.setenv('OPSKIT_PROXY_TOKEN', 'synthetic-proxy-fixture')
        auth = base64.b64encode(b'opskit:synthetic-proxy-fixture').decode()
        with serving(egress.Server(('127.0.0.1', 0), egress.Proxy)) as address:
            with socket.create_connection(address, timeout=3) as client:
                client.settimeout(3)
                if mode in ('connect', 'wrong-port'):
                    authority = target if mode == 'connect' else f'127.0.0.1:{peer_port % 65535 + 1}'
                    request = f'CONNECT {authority} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n'
                else:
                    host = target if mode != 'wrong-host-header' else 'other.example:80'
                    extra = f'Host: {target}\r\n' if mode == 'duplicate-host' else ''
                    request = f'GET http://{target}/probe HTTP/1.1\r\nHost: {host}\r\n{extra}Proxy-Authorization: Basic {auth}\r\n\r\n'
                client.sendall(request.encode())
                response = read_headers(client)
                if mode == 'connect':
                    assert b'200 Connection established' in response
                    client.sendall(f'GET /probe HTTP/1.1\r\nHost: {target}\r\nConnection: close\r\n\r\n'.encode())
                    response = read_headers(client)
                if mode in ('connect', 'http'):
                    assert b'200 OK' in response
                else:
                    assert b'403 Forbidden' in response
        assert requests == (['/probe'] if mode in ('connect', 'http') else [])
