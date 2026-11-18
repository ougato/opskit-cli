"""Sign release assets with an operator-owned Ed25519 PEM key; never create keys."""
import argparse
import base64
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.release_signature import file_digest, payload


def sign(path: Path, build: int, key):
    digest = file_digest(path)
    message = payload(build, path.name, digest)
    signature = key.sign(message)
    envelope = {"build": build, "filename": path.name, "sha256": digest,
                "signature": base64.b64encode(signature).decode("ascii")}
    path.with_name(path.name + ".sig.json").write_text(json.dumps(envelope), encoding="utf-8")
    path.with_name(path.name + ".signed").write_bytes(message)
    path.with_name(path.name + ".sig").write_bytes(signature)


if __name__ == "__main__":
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=int)
    parser.add_argument("assets", nargs="+", type=Path)
    args = parser.parse_args()
    key = load_pem_private_key(os.environ["OPSKIT_RELEASE_SIGNING_KEY"].encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("release key must be Ed25519")
    expected_public = os.environ.get("OPSKIT_RELEASE_PUBLIC_KEY", "")
    if not expected_public:
        raise ValueError("pinned release public key is required")
    actual_public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    if actual_public != expected_public:
        raise ValueError("signing key does not match the pinned release public key")
    for asset in args.assets:
        sign(asset, args.build, key)
