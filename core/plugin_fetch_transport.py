"""Small shared guest-only transport primitives; never load host state."""
import ipaddress
import os
import re


def private_write(path, body):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(body)


def quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t') + '"'


def proxy_binding(proxy):
    try:
        if not isinstance(proxy, dict) or set(proxy) != {"address", "port", "token"}:
            raise ValueError()
        if not isinstance(proxy["address"], str):
            raise ValueError()
        address = ipaddress.IPv4Address(proxy["address"])
        if (address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or address.is_reserved
                or type(proxy["port"]) is not int or not 1 <= proxy["port"] <= 65535
                or not isinstance(proxy["token"], str) or re.fullmatch(r"[0-9a-f]{64}", proxy["token"]) is None):
            raise ValueError()
        return str(address), proxy["port"], proxy["token"]
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid fetch proxy binding") from None
