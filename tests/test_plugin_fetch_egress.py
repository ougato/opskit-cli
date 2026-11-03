"""Fetch target checks precede DNS and retain private destination protection."""
import socket

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
