"""Official network commands; business options stay in this module."""
import ipaddress
import socket
import subprocess

import typer
from core.cli import emit, fail, require_interaction, guarded
from network import commands


def parse_ports(value):
    ports = set()
    try:
        for part in value.split(","):
            ends = part.strip().split("-")
            if len(ends) == 1:
                start = end = int(ends[0])
            elif len(ends) == 2:
                start, end = map(int, ends)
            else:
                raise ValueError()
            if not 1 <= start <= end <= 65535:
                raise ValueError()
            ports.update(range(start, end + 1))
    except ValueError:
        fail("ports must be numbers or ascending ranges within 1..65535", 2)
    return sorted(ports)


def missing_host(host, menu_name):
    if host is None:
        require_interaction()
        from network import menu
        getattr(menu, menu_name)(host=None, pause_after=True)
        return True
    if not host.strip() or host.startswith("-"):
        fail("invalid host", 2)
    return False


def register(app):
    @app.command("ping")
    @guarded
    def ping(host: str = typer.Argument(None), count: int = typer.Option(4, "--count", min=1, max=10000), timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600)):
        if missing_host(host, "show_ping"):
            return
        result = commands.ping(host, count=count, timeout=timeout, strict=True)
        if not result.reachable:
            emit(result, error={"code": 1, "message": "ping target is unreachable"})
            raise typer.Exit(1)
        emit(result)

    @app.command("traceroute")
    @guarded
    def traceroute(host: str = typer.Argument(None), max_hops: int = typer.Option(20, "--max-hops", min=1, max=255), timeout: float = typer.Option(120, "--timeout", min=0.1, max=3600)):
        if missing_host(host, "show_traceroute"):
            return
        iterator = commands.traceroute(host, max_hops=max_hops, timeout=timeout, strict=True)
        try:
            while True:
                try:
                    hop = next(iterator)
                except StopIteration as done:
                    result = {"event": "complete", **done.value}
                    if not result["reached"]:
                        emit(result, error={"code": 1, "message": "traceroute target was not reached"})
                        raise typer.Exit(1)
                    emit(result)
                    break
                emit({"host": host, "hop": hop})
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            emit({"event": "complete", "host": host, "status": "cancelled"},
                 error={"code": 130, "message": "traceroute cancelled"})
            raise typer.Exit(130)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            status = "timeout" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "failed"
            emit({"event": "complete", "host": host, "status": status,
                  "return_code": getattr(exc, "returncode", None)},
                 error={"code": 1, "message": "traceroute " + status})
            raise typer.Exit(1)
        finally:
            iterator.close()

    @app.command("dns")
    @guarded
    def dns(host: str = typer.Argument(None), reverse: bool = typer.Option(False, "--reverse"), family: str = typer.Option("any", "--family"), timeout: float = typer.Option(10, "--timeout", min=0.1, max=3600)):
        if family not in {"any", "ipv4", "ipv6"}:
            fail("family must be any, ipv4 or ipv6", 2)
        if missing_host(host, "show_dns"):
            return
        try:
            ipaddress.ip_address(host)
            reverse = True
        except ValueError:
            if reverse:
                fail("reverse DNS requires an IP address", 2)
        if reverse:
            if family != "any":
                fail("--family applies only to forward DNS; reverse DNS requires any", 2)
            result = commands.dns_reverse(host, timeout=timeout)
            if not result:
                fail("PTR record not found")
            emit({"address": host, "hostname": result})
        else:
            result = commands.dns_lookup(host, {"any": socket.AF_UNSPEC, "ipv4": socket.AF_INET, "ipv6": socket.AF_INET6}[family], timeout=timeout)
            if not result.addresses:
                fail("DNS lookup failed")
            emit(result)

    @app.command("port-scan")
    @guarded
    def port_scan(host: str = typer.Argument(None), ports: str = typer.Option("", "--ports"), timeout: float = typer.Option(0.5, "--timeout", min=0.01, max=60), concurrency: int = typer.Option(16, "--concurrency", min=1, max=50), total_timeout: float = typer.Option(30, "--total-timeout", min=0.1, max=3600)):
        selected = parse_ports(ports) if ports else commands.COMMON_TCP_PORTS
        if missing_host(host, "show_port_scan"):
            return
        emit(commands.scan_ports(host, selected, timeout=timeout, concurrency=concurrency, total_timeout=total_timeout))

    @app.command("speed-test")
    @guarded
    def speed_test(url: str = typer.Option("", "--url"), duration: float = typer.Option(15, "--duration", min=0.1, max=300)):
        from urllib.parse import urlsplit
        from core.constants import SPEED_TEST_URL
        url = url or SPEED_TEST_URL
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            fail("URL must be HTTP(S) without credentials", 2)
        import httpx
        try:
            emit(commands.download_measurement(url, duration))
        except httpx.HTTPError as exc:
            fail(type(exc).__name__)

    @app.command("public-ip")
    @guarded
    def public_ip(timeout: float = typer.Option(10, "--timeout", min=0.1, max=60)):
        result = commands.get_public_ip(timeout=timeout)
        if result is None:
            fail("public IP lookup failed")
        emit({"address": result})
