"""Exercise real signatures and a stateful gh protocol without external writes."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
import pytest
import yaml

from core.release_signature import file_digest, payload


SPEC = importlib.util.spec_from_file_location(
    "release_assets", Path(__file__).resolve().parents[1] / "scripts/release_assets.py")
ra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ra)
COMMIT = "a" * 40


@pytest.fixture
def artifacts(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    root = tmp_path / "artifacts"
    for name, (platform, arch, binary) in ra.MATRIX.items():
        directory = root / name
        directory.mkdir(parents=True)
        path = directory / binary
        path.write_bytes(name.encode())
        digest = file_digest(path)
        (directory / (binary + ".sha256")).write_text(f"{digest}  {binary}\n", encoding="utf-8")
        (directory / "manifest.json").write_text(json.dumps({
            "version": "7", "platform": platform, "arch": arch, "binary": binary,
            "sha256": digest, "size": path.stat().st_size}), encoding="utf-8")
    return root, key, public


def sign_all(artifacts):
    root, key, public = artifacts
    ra.assets(root, 7, signed=False)
    for name, (_, _, binary) in ra.MATRIX.items():
        path = root / name / binary
        digest = file_digest(path)
        message = payload(7, binary, digest)
        signature = key.sign(message)
        (path.parent / (binary + ".sig.json")).write_text(json.dumps({
            "build": 7, "filename": binary, "sha256": digest,
            "signature": base64.b64encode(signature).decode()}), encoding="utf-8")
        (path.parent / (binary + ".signed")).write_bytes(message)
        (path.parent / (binary + ".sig")).write_bytes(signature)
    return ra.assets(root, 7, signed=True, public_key=public)


class FakeGH:
    """Run the production gh adapter through HTTP framing and CLI upload calls."""

    def __init__(self):
        self.release = None
        self.remote = {}
        self.calls = []
        self.commit = COMMIT
        self.upload_failure = None
        self.corrupt_after_upload = False
        self.status = None
        self.other_releases = []
        self.lose_upload_ack = False

    def asset(self, path):
        return {"name": path.name, "state": "uploaded", "size": path.stat().st_size,
                "digest": "sha256:" + file_digest(path)}

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:3] == ["gh", "release", "upload"]:
            assert "--clobber" not in args
            path = Path(args[4])
            if path.name in self.remote:
                return SimpleNamespace(returncode=1, stdout="", stderr="exists")
            if self.upload_failure == path.name:
                return SimpleNamespace(returncode=1, stdout="", stderr="injected upload failure")
            self.remote[path.name] = self.asset(path)
            if self.lose_upload_ack:
                self.lose_upload_ack = False
                return SimpleNamespace(returncode=1, stdout="", stderr="lost response")
            if self.corrupt_after_upload:
                self.remote[path.name]["digest"] = "sha256:" + "0" * 64
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert args[:3] == ["gh", "api", "--include"]
        method = args[4]
        endpoint = args[5].removeprefix("repos/owner/repository/")
        status = self.status or 200
        if status != 200:
            body = {"message": "injected"}
        elif endpoint == "git/ref/tags/v7":
            body = {"object": {"type": "commit", "sha": self.commit}}
        elif endpoint.startswith("releases?per_page=100&page="):
            body = self.other_releases + ([] if self.release is None else [dict(self.release)])
        elif method == "POST" and endpoint == "releases":
            data = json.loads(kwargs["input"])
            assert data["draft"] is True
            assert data["make_latest"] == "false"
            self.release = dict(data, id=17)
            body = dict(self.release)
        elif endpoint == "releases/17/assets?per_page=100":
            body = list(self.remote.values())
        elif method == "PATCH" and endpoint == "releases/17":
            data = json.loads(kwargs["input"])
            assert len(self.remote) == 20
            self.release.update(data)
            body = dict(self.release)
        else:
            raise AssertionError((method, endpoint))
        return SimpleNamespace(returncode=0 if status == 200 else 1,
                               stdout=f"HTTP/2.0 {status} Test\r\nContent-Type: application/json\r\n\r\n" + json.dumps(body),
                               stderr="")

    def api(self):
        return ra.GitHub("owner/repository", run=self.run)

    def writes(self):
        return [args for args, _ in self.calls if
                args[:3] == ["gh", "release", "upload"] or args[4] != "GET"]


def test_complete_matrix_with_real_signatures_publishes_last(artifacts):
    expected = sign_all(artifacts)
    fake = FakeGH()
    ra.publish(fake.api(), "v7", COMMIT, expected)
    assert fake.release["draft"] is False
    assert fake.release["make_latest"] == "true"
    assert set(fake.remote) == set(expected)
    assert fake.calls[-1][0][4] == "PATCH"
    fake.calls.clear()
    ra.publish(fake.api(), "v7", COMMIT, expected)
    assert fake.writes() == []  # Identical public release: no mutation on retry.


def test_failed_upload_retains_draft_and_retry_only_fills_missing(artifacts):
    expected = sign_all(artifacts)
    fake = FakeGH()
    fake.upload_failure = sorted(expected)[4]
    with pytest.raises(RuntimeError, match="draft retained"):
        ra.publish(fake.api(), "v7", COMMIT, expected)
    assert fake.release["draft"] is True
    assert len(fake.remote) == 4
    retained = dict(fake.remote)
    fake.upload_failure = None
    fake.calls.clear()
    ra.publish(fake.api(), "v7", COMMIT, expected)
    assert fake.release["draft"] is False
    assert all(fake.remote[name] == entry for name, entry in retained.items())
    uploads = [args[4] for args, _ in fake.calls if args[:3] == ["gh", "release", "upload"]]
    assert len(uploads) == 16


def test_completed_upload_with_lost_ack_is_recovered_without_replacement(artifacts):
    expected = sign_all(artifacts)
    fake = FakeGH()
    fake.lose_upload_ack = True
    with pytest.raises(RuntimeError, match="draft retained"):
        ra.publish(fake.api(), "v7", COMMIT, expected)
    assert len(fake.remote) == 1
    assert fake.release["draft"] is True
    fake.calls.clear()
    ra.publish(fake.api(), "v7", COMMIT, expected)
    assert len([args for args, _ in fake.calls if args[:3] == ["gh", "release", "upload"]]) == 19
    assert fake.release["draft"] is False


def test_incomplete_public_release_is_never_filled_in_place(artifacts):
    fake = FakeGH()
    fake.release = {"id": 17, "tag_name": "v7", "draft": False, "prerelease": False}
    with pytest.raises(ValueError, match="incomplete"):
        ra.publish(fake.api(), "v7", COMMIT, sign_all(artifacts))
    assert fake.writes() == []


@pytest.mark.parametrize("draft", [True, False])
@pytest.mark.parametrize("change", ["digest", "size", "state", "missing-digest", "unknown"])
def test_existing_conflict_never_overwrites_or_publishes(artifacts, draft, change):
    expected = sign_all(artifacts)
    fake = FakeGH()
    fake.release = {"id": 17, "tag_name": "v7", "draft": draft, "prerelease": False}
    fake.remote = {name: fake.asset(path) for name, path in expected.items()}
    entry = fake.remote[next(iter(expected))]
    if change == "missing-digest":
        del entry["digest"]
    elif change == "unknown":
        entry["name"] = "unexpected"
    else:
        entry[change] = {"digest": "sha256:" + "0" * 64, "size": 0, "state": "starter"}[change]
    with pytest.raises(ValueError):
        ra.publish(fake.api(), "v7", COMMIT, expected)
    assert fake.writes() == []


def test_remote_digest_rechecked_before_publishing(artifacts):
    fake = FakeGH()
    fake.corrupt_after_upload = True
    with pytest.raises(ValueError, match="refusing replacement"):
        ra.publish(fake.api(), "v7", COMMIT, sign_all(artifacts))
    assert fake.release["draft"] is True
    assert all(args[4] != "PATCH" for args, _ in fake.calls if args[1] == "api")


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_api_failures_do_not_become_creation_permission(artifacts, status):
    fake = FakeGH()
    fake.status = status
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        ra.publish(fake.api(), "v7", COMMIT, sign_all(artifacts))
    assert fake.writes() == []


def test_changed_tag_is_rejected_without_writes(artifacts):
    fake = FakeGH()
    fake.commit = "b" * 40
    with pytest.raises(ValueError, match="event commit"):
        ra.publish(fake.api(), "v7", COMMIT, sign_all(artifacts))
    assert fake.writes() == []


def test_older_release_does_not_replace_newer_latest(artifacts):
    fake = FakeGH()
    fake.other_releases = [{"id": 18, "tag_name": "v8", "draft": False, "prerelease": False}]
    ra.publish(fake.api(), "v7", COMMIT, sign_all(artifacts))
    assert fake.release["draft"] is False
    assert fake.release["make_latest"] == "false"


@pytest.mark.parametrize("change", ["missing-platform", "extra-file", "wrong-checksum", "wrong-version", "wrong-size"])
def test_malformed_unsigned_artifacts_refused(artifacts, change):
    root, _, _ = artifacts
    directory = root / "opskit-linux-x64"
    if change == "missing-platform":
        (root / "opskit-linux-arm64").rename(root / "unexpected")
    elif change == "extra-file":
        (directory / "private-debug.txt").write_text("must not publish")
    elif change == "wrong-checksum":
        (directory / "opskit-linux-x64.sha256").write_text("0" * 64)
    else:
        path = directory / "manifest.json"
        data = json.loads(path.read_text())
        data["version" if change == "wrong-version" else "size"] = "wrong"
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ra.assets(root, 7, signed=False)


@pytest.mark.parametrize("suffix", ["", ".sig.json", ".signed", ".sig"])
def test_modified_signed_asset_refused(artifacts, suffix):
    sign_all(artifacts)
    root, _, public = artifacts
    path = root / "opskit-linux-x64" / ("opskit-linux-x64" + suffix)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        ra.assets(root, 7, signed=True, public_key=public)


def test_real_signing_cli_produces_the_validated_asset_set(artifacts):
    root, key, public = artifacts
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    env = dict(os.environ, OPSKIT_RELEASE_SIGNING_KEY=pem, OPSKIT_RELEASE_PUBLIC_KEY=public)
    binaries = [root / name / binary for name, (_, _, binary) in ra.MATRIX.items()]
    result = subprocess.run([sys.executable, "-I", str(Path(SPEC.origin).with_name("sign_release.py")),
                             "--build", "7", *map(str, binaries)], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert len(ra.assets(root, 7, signed=True, public_key=public)) == 20


def test_unbounded_metadata_refused_before_json_read(artifacts):
    root, _, _ = artifacts
    manifest = root / "opskit-linux-x64/manifest.json"
    manifest.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="exceeds 1 MiB"):
        ra.assets(root, 7, signed=False)


def test_validator_matrix_matches_actual_workflow():
    workflow = yaml.safe_load((Path(SPEC.origin).parents[1] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    builds = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {build["artifact"] for build in builds} == set(ra.MATRIX)


def test_signing_cli_without_pinned_public_key_writes_nothing(artifacts):
    root, key, _ = artifacts
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    env = dict(os.environ, OPSKIT_RELEASE_SIGNING_KEY=pem)
    env.pop("OPSKIT_RELEASE_PUBLIC_KEY", None)
    path = root / "opskit-linux-x64/opskit-linux-x64"
    result = subprocess.run([sys.executable, "-I", str(Path(SPEC.origin).with_name("sign_release.py")),
                             "--build", "7", str(path)], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "pinned release public key is required" in result.stderr
    assert not list(root.rglob("*.sig*"))
    assert not list(root.rglob("*.signed"))
