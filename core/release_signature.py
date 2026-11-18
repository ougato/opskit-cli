"""Ed25519 release envelopes bound to build, platform asset and byte digest."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path


def payload(build: int, filename: str, sha256: str) -> bytes:
    if type(build) is not int or build <= 0 or Path(filename).name != filename:
        raise ValueError("invalid release identity")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("invalid release digest")
    return json.dumps({"build": build, "filename": filename, "sha256": sha256},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify(envelope: dict, public_key: str, *, build: int, filename: str, digest: str) -> bool:
    """A missing pinned key is never replaced by a key from the download."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if not public_key or set(envelope) != {"build", "filename", "sha256", "signature"}:
            return False
        if (type(envelope["build"]) is not int or envelope["build"] != build
                or envelope["filename"] != filename or envelope["sha256"] != digest):
            return False
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(base64.b64decode(envelope["signature"], validate=True), payload(build, filename, digest))
        return True
    except (ValueError, TypeError, KeyError, ImportError):
        return False
    except Exception:
        # Includes InvalidSignature, without leaking signature or key material.
        return False


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
