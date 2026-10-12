import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.release_signature import file_digest, payload, verify


def test_signature_binds_asset_version_and_content(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    asset = tmp_path / "opskit-linux-x64"
    asset.write_bytes(b"release")
    digest = file_digest(asset)
    envelope = {"build": 100, "filename": asset.name, "sha256": digest,
                "signature": base64.b64encode(key.sign(payload(100, asset.name, digest))).decode()}
    assert verify(envelope, public, build=100, filename=asset.name, digest=digest)
    for key_value, build, name, checksum in [("", 100, asset.name, digest),
                                            (public, 99, asset.name, digest),
                                            (public, 100, "another", digest),
                                            (public, 100, asset.name, "a" * 64)]:
        assert not verify(envelope, key_value, build=build, filename=name, digest=checksum)
    envelope["signature"] = "a" * 88
    assert not verify(envelope, public, build=100, filename=asset.name, digest=digest)


def test_apply_rejects_unsigned_pending_without_touching_binary(tmp_path, monkeypatch):
    from core import updater
    pending = tmp_path / "pending"
    pending.write_bytes(b"untrusted")
    touched = []
    monkeypatch.setattr(updater, "_self_path", lambda: touched.append(True))
    assert not updater._do_apply(pending, updater.APP_VERSION + 1)
    assert not touched


def test_pending_rechecks_signature_and_digest(tmp_path, monkeypatch):
    from core import updater, config
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(config, "load_config", lambda: {"update": {"signing_public_key": public}})
    monkeypatch.setattr(updater, "_asset_filename", lambda: "opskit-test")
    pending = tmp_path / "pending"
    pending.write_bytes(b"signed release")
    build = updater.APP_VERSION + 1
    digest = file_digest(pending)
    envelope = {"build": build, "filename": "opskit-test", "sha256": digest,
                "signature": base64.b64encode(key.sign(payload(build, "opskit-test", digest))).decode()}
    pending.with_name("pending.sig.json").write_text(json.dumps(envelope))
    assert updater._verify_pending_signature(pending, build)
    pending.write_bytes(b"tampered release")
    assert not updater._verify_pending_signature(pending, build)
    assert not updater._verify_pending_signature(pending, updater.APP_VERSION)
