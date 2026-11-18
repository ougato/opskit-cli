"""Exact destination and DNS policy for supervised native network brokers."""
import ipaddress
import re
import socket
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
