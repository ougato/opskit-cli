"""Bounded Git protocol v0 framing; pack objects are parsed only in isolation."""
import hashlib
import re

MAX_ADVERTISEMENT = 2 * 1024**2
MAX_PACK = 64 * 1024**2
OID = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def packet(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= 65516:
        raise ValueError("invalid Git packet")
    return f"{len(body) + 4:04x}".encode() + body


def _exact(stream, size):
    result = bytearray()
    while len(result) < size:
        body = stream.read(size - len(result))
        if not body:
            raise ValueError("truncated Git packet")
        result.extend(body)
    return bytes(result)


def _packet(stream, prefix=None):
    prefix = _exact(stream, 4) if prefix is None else prefix
    if re.fullmatch(rb"[0-9a-fA-F]{4}", prefix) is None:
        raise ValueError("invalid Git packet length")
    size = int(prefix, 16)
    if size == 0:
        return None
    if not 4 < size <= 65520:
        raise ValueError("invalid Git packet size")
    return _exact(stream, size - 4)


def read_advertisement(stream):
    """Read one bounded SSH ref advertisement without waiting for stream EOF."""
    raw = bytearray()
    while True:
        prefix = _exact(stream, 4)
        body = _packet(stream, prefix)
        if len(raw) + 4 + (len(body) if body is not None else 0) > MAX_ADVERTISEMENT:
            raise ValueError("Git advertisement budget exceeded")
        raw.extend(prefix)
        if body is None:
            return bytes(raw)
        raw.extend(body)


def advertisement(raw, branch=None, *, http=True):
    """Select an exact advertised branch or HEAD, never an arbitrary first ref."""
    import io
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ADVERTISEMENT:
        raise ValueError("Git advertisement budget exceeded")
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
                               or branch.startswith("-") or any(ord(c) < 33 for c in branch)):
        raise ValueError("invalid requested Git branch")
    stream = io.BytesIO(raw)
    if http and (_packet(stream) != b"# service=git-upload-pack\n" or _packet(stream) is not None):
        raise ValueError("unsupported Git service")
    refs, capabilities = {}, []
    while True:
        body = _packet(stream)
        if body is None:
            break
        if len(refs) >= 8192 or not body.endswith(b"\n"):
            raise ValueError("Git reference budget exceeded")
        line = body[:-1]
        if b"\0" in line:
            if refs:
                raise ValueError("unexpected Git capabilities")
            line, caps = line.split(b"\0", 1)
            capabilities = caps.split(b" ")
            if len(capabilities) > 256 or any(not cap or len(cap) > 512
                    or any(c < 33 or c > 126 for c in cap) for cap in capabilities):
                raise ValueError("invalid Git capabilities")
        oid, name = line.split(b" ", 1)
        if (not OID.fullmatch(oid) or oid == b"0" * len(oid) or not name or len(name) > 1024
                or any(c < 33 or c == 127 for c in name) or name in refs):
            raise ValueError("invalid Git reference")
        refs[name] = oid
    if stream.read(1):
        raise ValueError("unexpected Git advertisement content")
    selected = b"HEAD" if branch is None else b"refs/heads/" + branch.encode()
    if selected not in refs:
        raise ValueError("requested Git reference is unavailable")
    oid = refs[selected]
    if len(oid) == 64 and b"object-format=sha256" not in capabilities:
        raise ValueError("Git object format mismatch")
    if len(oid) == 40 and b"object-format=sha256" in capabilities:
        raise ValueError("Git object format mismatch")
    chosen = [cap for cap in (b"ofs-delta", b"no-progress", b"object-format=sha256") if cap in capabilities]
    shallow = b"shallow" in capabilities
    request = packet(b"want " + oid + (b" " + b" ".join(chosen) if chosen else b"") + b"\n")
    if shallow:
        request += packet(b"deepen 1\n")
    request += b"0000" + packet(b"done\n")
    return {"commit": oid.decode(), "shallow": shallow}, request


def receive_pack(stream, destination, selection):
    """Strip bounded negotiation framing; never decompress a Git object here."""
    commit = selection["commit"].encode()
    if not OID.fullmatch(commit) or type(selection.get("shallow")) is not bool:
        raise ValueError("invalid Git pack selection")
    shallow, consumed, nak, flushes = [], 0, False, 0
    while True:
        prefix = _exact(stream, 4)
        if prefix == b"PACK":
            if not nak:
                raise ValueError("Git pack without acknowledgement")
            break
        body = _packet(stream, prefix)
        consumed += 4 + (len(body) if body is not None else 0)
        if consumed > 65536 or nak:
            raise ValueError("Git negotiation budget exceeded")
        if body is None:
            flushes += 1
            if flushes > 4:
                raise ValueError("unexpected Git negotiation flush")
        elif body == b"NAK\n":
            nak = True
        elif body.startswith(b"shallow ") and selection["shallow"]:
            # upload-pack may omit LF for shallow boundary pkt-lines.
            oid = body[8:].removesuffix(b"\n")
            if not OID.fullmatch(oid) or len(oid) != len(commit) or oid.decode() in shallow or len(shallow) >= 64:
                raise ValueError("invalid shallow Git boundary")
            shallow.append(oid.decode())
        else:
            raise ValueError("unexpected Git negotiation response")
    digest, count = hashlib.sha256(), 4
    destination.write(b"PACK")
    digest.update(b"PACK")
    while body := stream.read(65536):
        count += len(body)
        if count > MAX_PACK:
            raise ValueError("Git pack size limit exceeded")
        destination.write(body)
        digest.update(body)
    if count < 12 + len(commit) // 2:
        raise ValueError("truncated Git pack")
    destination.flush()
    return {"commit": commit.decode(), "shallow": shallow, "bytes": count, "sha256": digest.hexdigest()}
