"""Pure remote-source and explicit authentication policy; never reads host config."""
import base64
from dataclasses import dataclass, field
import hashlib
import json
import re
import struct
import unicodedata
from urllib.parse import quote, unquote, urlsplit

from core.plugin_egress import fetch_target


@dataclass(frozen=True)
class RemoteSource:
    kind: str
    host: str
    port: int
    user: str
    path: str
    relative: bool = False

    @property
    def authority(self):
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

    @property
    def url(self):
        if self.relative:
            host = f"[{self.host}]" if ":" in self.host else self.host
            return f"{self.user}@{host}:{self.path}"
        authority = (self.user + "@" if self.user else "") + self.authority
        return f"{self.kind}://{authority}{quote(self.path, safe='/~._-')}"

    @property
    def identity(self):
        # Relative SCP paths and absolute SSH URLs deliberately remain distinct.
        body = json.dumps([self.kind, self.host, self.port, self.user, self.path, self.relative],
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(body).hexdigest()


def remote_source(value):
    """Canonical HTTPS/SSH source; rejects inline secrets and ambiguous paths."""
    try:
        if (not isinstance(value, str) or not 0 < len(value) <= 4096
                or len(value.encode("utf-8")) > 4096 or value != value.strip()
                or any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in value)):
            raise ValueError()
        relative = False
        if "://" not in value:
            match = re.fullmatch(r"([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})@(\[[^\]]+\]|[^:/@]+):(.+)", value)
            if match is None:
                raise ValueError()
            user, authority, path = match.groups()
            host, port = fetch_target(authority + ":22")
            kind, relative = "ssh", not path.startswith("/")
        else:
            parsed = urlsplit(value)
            kind = parsed.scheme.lower()
            if (kind not in ("https", "ssh") or not parsed.hostname or parsed.password is not None
                    or parsed.query or parsed.fragment or "?" in value or "#" in value):
                raise ValueError()
            user = parsed.username or ""
            if kind == "https" and parsed.username is not None:
                raise ValueError()
            if kind == "ssh" and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", user) is None:
                raise ValueError()
            port = parsed.port or (443 if kind == "https" else 22)
            if parsed.port == 0:
                raise ValueError()
            authority = f"[{parsed.hostname}]:{port}" if ":" in parsed.hostname else f"{parsed.hostname}:{port}"
            host, port = fetch_target(authority)
            if re.search(r"%(?![0-9A-Fa-f]{2})|%(?:2f|5c)", parsed.path, re.IGNORECASE):
                raise ValueError()
            path = unquote(parsed.path, encoding="utf-8", errors="strict")
            if not path.startswith("/"):
                raise ValueError()
        path = path.removesuffix("/")
        parts = path.split("/")[0 if relative else 1:]
        if (not parts or relative and path.startswith("-") or any(not p or p in (".", "..") for p in parts)
                or any(c == "\\" or ord(c) < 32 or ord(c) == 127
                       or unicodedata.category(c) == "Cf" for c in path)):
            raise ValueError()
        source = RemoteSource(kind, host, port, user, path, relative)
        if len(source.url.encode()) > 4096:
            raise ValueError()
        return source
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("invalid remote fetch source") from None


@dataclass(frozen=True)
class FetchAuthentication:
    kind: str
    source_id: str
    _values: tuple = field(default=(), repr=False)

    def payload(self):
        """Sensitive wire material; never use in logs, argv or plugin metadata."""
        return {"kind": self.kind, "source_id": self.source_id, **dict(self._values)}


def _text(value, limit, *, empty=False):
    if (not isinstance(value, str) or len(value) > limit or len(value.encode()) > limit
            or not empty and not value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError()
    return value


def _pem(value, labels, limit):
    if not isinstance(value, str) or not 0 < len(value) <= limit or not value.isascii():
        raise ValueError()
    label = "|".join(re.escape(item) for item in labels)
    pattern = rf"-----BEGIN ({label})-----\r?\n([A-Za-z0-9+/=\r\n]+)-----END \1-----\s*"
    position, count = 0, 0
    for match in re.finditer(pattern, value):
        if match.start() != position:
            raise ValueError()
        base64.b64decode(re.sub(r"\s", "", match[2]), validate=True)
        position = match.end()
        count += 1
    if position != len(value) or not position or labels != ["CERTIFICATE"] and count != 1:
        raise ValueError()
    return value


def _host_keys(source, keys):
    if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
        raise ValueError()
    host = source.host if source.port == 22 else f"[{source.host}]:{source.port}"
    records = []
    for key in keys:
        if not isinstance(key, str) or len(key) > 8192:
            raise ValueError()
        algorithm, encoded = key.split()
        if algorithm not in {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}:
            raise ValueError()
        decoded = base64.b64decode(encoded, validate=True)
        fields, offset = [], 0
        while offset < len(decoded) and len(fields) < 4:
            if offset + 4 > len(decoded):
                raise ValueError()
            length, = struct.unpack(">I", decoded[offset:offset + 4])
            offset += 4
            if not length or offset + length > len(decoded):
                raise ValueError()
            fields.append(decoded[offset:offset + length])
            offset += length
        if offset != len(decoded) or not fields or fields[0] != algorithm.encode():
            raise ValueError()
        if algorithm == "ssh-ed25519":
            valid = len(fields) == 2 and len(fields[1]) == 32
        elif algorithm == "ecdsa-sha2-nistp256":
            valid = (len(fields) == 3 and fields[1] == b"nistp256"
                     and len(fields[2]) == 65 and fields[2][0] == 4)
        else:
            valid = len(fields) == 3 and all(not value[0] & 0x80 for value in fields[1:])
            if valid:
                exponent, modulus = (int.from_bytes(value, "big") for value in fields[1:])
                valid = 3 <= exponent <= 2**32 and exponent % 2 == 1 and 2048 <= modulus.bit_length() <= 8192
        if not valid:
            raise ValueError()
        records.append(f"{host} {algorithm} {encoded}\n")
    if len(set(records)) != len(records):
        raise ValueError()
    return "".join(records)


def authentication(source, profile=None):
    """Bind an explicit profile to a full source before any network operation.

    This validates bounded framing/scope, not PEM cryptography or a live server.
    Transport adapters must enforce TLS/host keys and disable redirects/helpers.
    """
    try:
        if not isinstance(source, RemoteSource) or remote_source(source.url) != source:
            raise ValueError()
        if profile is None:
            if source.kind != "https":
                raise ValueError()
            return FetchAuthentication("anonymous", source.identity)
        if (not isinstance(profile, dict) or set(profile) != {"schema_version", "source", "authentication"}
                or type(profile["schema_version"]) is not int or profile["schema_version"] != 1
                or remote_source(profile["source"]) != source):
            raise ValueError()
        auth = profile["authentication"]
        if not isinstance(auth, dict):
            raise ValueError()
        kind = auth.get("kind")
        values = {}
        if source.kind == "https":
            fields = {"anonymous": set(), "bearer": {"token"}, "basic": {"username", "password"}}
            if kind not in fields or set(auth) - {"kind", "ca_pem"} != fields[kind]:
                raise ValueError()
            if kind == "bearer":
                token = _text(auth["token"], 4096)
                if re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is None:
                    raise ValueError()
                values["authorization"] = "Bearer " + token
            elif kind == "basic":
                user = _text(auth["username"], 1024, empty=True)
                password = _text(auth["password"], 4096, empty=True)
                if ":" in user:
                    raise ValueError()
                values["authorization"] = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
            if "ca_pem" in auth:
                values["ca_pem"] = _pem(auth["ca_pem"], ["CERTIFICATE"], 32768)
        else:
            if kind != "ssh_key" or set(auth) - {"kind", "passphrase"} != {"private_key", "host_keys"}:
                raise ValueError()
            values["private_key"] = _pem(auth["private_key"], ["OPENSSH PRIVATE KEY", "PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "ENCRYPTED PRIVATE KEY"], 32768)
            values["known_hosts"] = _host_keys(source, auth["host_keys"])
            values["passphrase"] = _text(auth.get("passphrase", ""), 4096, empty=True)
        result = FetchAuthentication(kind, source.identity, tuple(sorted(values.items())))
        if len(json.dumps(result.payload(), ensure_ascii=False).encode()) > 65536:
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("invalid fetch authentication profile or source binding") from None
