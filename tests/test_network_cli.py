import json
import socket

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def invoke(*args):
    import main
    return runner.invoke(main.app, ["--output", "json", "network", *args])


def test_real_local_port_scan():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = invoke("port-scan", "127.0.0.1", "--ports", str(port), "--concurrency", "1")
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]
        assert data[0]["port"] == port and data[0]["open"]


def test_dns_localhost_ipv4():
    result = invoke("dns", "localhost", "--family", "ipv4")
    assert result.exit_code == 0
    assert "127.0.0.1" in json.loads(result.stdout)["data"]["addresses"]


@pytest.mark.parametrize("value", ["0", "65536", "80-22", "x", "1-2-3"])
def test_invalid_ports(value):
    result = invoke("port-scan", "localhost", "--ports", value)
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_missing_host_never_prompts():
    result = invoke("ping")
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_public_ip_failure_is_error(monkeypatch):
    from network import commands
    monkeypatch.setattr(commands, "get_public_ip", lambda **kwargs: None)
    result = invoke("public-ip", "--timeout", "1")
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]


def test_ipv6_automatically_uses_reverse(monkeypatch):
    from network import commands
    monkeypatch.setattr(commands, "dns_reverse", lambda address, **kwargs: "localhost")
    result = invoke("dns", "::1")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["hostname"] == "localhost"


def test_port_scan_total_deadline_is_one_error_not_partial_success(monkeypatch):
    from network import commands

    def deadline(host, ports, **kwargs):
        assert kwargs["total_timeout"] == 1
        assert kwargs["timeout"] == 0.5
        raise TimeoutError("port scan total deadline exceeded")

    monkeypatch.setattr(commands, "scan_ports", deadline)
    result = invoke("port-scan", "localhost", "--ports", "80", "--total-timeout", "1")
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert not payload["ok"] and payload["data"] is None


def test_unreachable_ping_has_failure_envelope_and_retains_measurement(monkeypatch):
    from network import commands
    monkeypatch.setattr(commands, "ping", lambda host, **kwargs: commands.PingResult(
        host, "127.0.0.1", 4, 0, 0, 0, 0, False))
    result = invoke("ping", "example.invalid")
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert not payload["ok"] and payload["error"]["code"] == 1
    assert payload["data"]["packets_recv"] == 0


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
@pytest.mark.parametrize("family", ["ipv4", "ipv6"])
@pytest.mark.parametrize("explicit_reverse", [False, True])
def test_reverse_family_conflict_fails_before_resolution(monkeypatch, address, family, explicit_reverse):
    from network import commands
    def forbidden(*args, **kwargs):
        pytest.fail("invalid DNS combination must not call resolver")
    monkeypatch.setattr(commands, "dns_reverse", forbidden)
    monkeypatch.setattr(commands, "dns_lookup", forbidden)
    result = invoke("dns", address, "--family", family, *(["--reverse"] if explicit_reverse else []))
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == 2 and payload["data"] is None
    assert "--family" in payload["error"]["message"]


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
@pytest.mark.parametrize("explicit_reverse", [False, True])
def test_reverse_accepts_explicit_any_and_passes_budget(monkeypatch, address, explicit_reverse):
    from network import commands
    seen = []
    def resolve(target, **kwargs):
        seen.append((target, kwargs))
        return "localhost"
    monkeypatch.setattr(commands, "dns_reverse", resolve)
    result = invoke("dns", address, "--family", "any", "--timeout", "1",
                    *(["--reverse"] if explicit_reverse else []))
    assert result.exit_code == 0, result.output
    assert seen == [(address, {"timeout": 1.0})]
