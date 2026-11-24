"""Exercise checksums and a stateful gh protocol without external writes."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.file_digest import file_digest


SPEC = importlib.util.spec_from_file_location(
    "release_assets", Path(__file__).resolve().parents[1] / "scripts/release_assets.py")
ra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ra)
COMMIT = "a" * 40


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path / "artifacts"
    for name, (platform, arch, binary) in ra.MATRIX.items():
        directory = root / name
        directory.mkdir(parents=True)
        path = directory / binary
        path.write_bytes(name.encode())
        digest = file_digest(path)
        (directory / (binary + ".sha256")).write_text(f"{digest}  {binary}\n", encoding="utf-8")
        manifest = {
            "version": "7", "platform": platform, "arch": arch, "binary": binary,
            "sha256": digest, "size": path.stat().st_size,
        }
        if name in ra.DIRECTORY_MATRIX:
            extension = ".exe" if platform == "windows" else ""
            manifest["directory"] = {
                asset: write_artifact(directory / asset) for asset in
                (f"{name}-launcher{extension}", f"{name}.tar.xz")
            }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def write_artifact(path):
    path.write_bytes(path.name.encode())
    digest = file_digest(path)
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return {"sha256": digest, "size": path.stat().st_size}


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
            assert len(self.remote) >= 4 * 2 + len(ra.DIRECTORY_MATRIX) * 4
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


def test_complete_matrix_with_checksums_publishes_last(artifacts):
    expected = ra.assets(artifacts, 7)
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
    expected = ra.assets(artifacts, 7)
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
    assert len(uploads) == len(expected) - len(retained)


def test_completed_upload_with_lost_ack_is_recovered_without_replacement(artifacts):
    expected = ra.assets(artifacts, 7)
    fake = FakeGH()
    fake.lose_upload_ack = True
    with pytest.raises(RuntimeError, match="draft retained"):
        ra.publish(fake.api(), "v7", COMMIT, expected)
    assert len(fake.remote) == 1
    assert fake.release["draft"] is True
    fake.calls.clear()
    ra.publish(fake.api(), "v7", COMMIT, expected)
    assert len([args for args, _ in fake.calls if args[:3] == ["gh", "release", "upload"]]) == len(expected) - 1
    assert fake.release["draft"] is False


def test_incomplete_public_release_is_never_filled_in_place(artifacts):
    fake = FakeGH()
    fake.release = {"id": 17, "tag_name": "v7", "draft": False, "prerelease": False}
    with pytest.raises(ValueError, match="incomplete"):
        ra.publish(fake.api(), "v7", COMMIT, ra.assets(artifacts, 7))
    assert fake.writes() == []


@pytest.mark.parametrize("draft", [True, False])
@pytest.mark.parametrize("change", ["digest", "size", "state", "missing-digest", "unknown"])
def test_existing_conflict_never_overwrites_or_publishes(artifacts, draft, change):
    expected = ra.assets(artifacts, 7)
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
        ra.publish(fake.api(), "v7", COMMIT, ra.assets(artifacts, 7))
    assert fake.release["draft"] is True
    assert all(args[4] != "PATCH" for args, _ in fake.calls if args[1] == "api")


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_api_failures_do_not_become_creation_permission(artifacts, status):
    fake = FakeGH()
    fake.status = status
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        ra.publish(fake.api(), "v7", COMMIT, ra.assets(artifacts, 7))
    assert fake.writes() == []


def test_changed_tag_is_rejected_without_writes(artifacts):
    fake = FakeGH()
    fake.commit = "b" * 40
    with pytest.raises(ValueError, match="event commit"):
        ra.publish(fake.api(), "v7", COMMIT, ra.assets(artifacts, 7))
    assert fake.writes() == []


def test_older_release_does_not_replace_newer_latest(artifacts):
    fake = FakeGH()
    fake.other_releases = [{"id": 18, "tag_name": "v8", "draft": False, "prerelease": False}]
    ra.publish(fake.api(), "v7", COMMIT, ra.assets(artifacts, 7))
    assert fake.release["draft"] is False
    assert fake.release["make_latest"] == "false"


@pytest.mark.parametrize("change", ["missing-platform", "extra-file", "wrong-checksum", "wrong-version", "wrong-size"])
def test_malformed_artifacts_refused(artifacts, change):
    root = artifacts
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
        ra.assets(root, 7)


@pytest.mark.parametrize("suffix", ["", ".sha256"])
def test_modified_asset_refused(artifacts, suffix):
    ra.assets(artifacts, 7)
    root = artifacts
    path = root / "opskit-linux-x64" / ("opskit-linux-x64" + suffix)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        ra.assets(root, 7)


def test_unbounded_metadata_refused_before_json_read(artifacts):
    root = artifacts
    manifest = root / "opskit-linux-x64/manifest.json"
    manifest.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size out of bounds"):
        ra.assets(root, 7)


@pytest.mark.parametrize("base, valid", [(6, True), (7, False), (8, False)])
def test_optional_delta_must_reference_an_earlier_build(artifacts, base, valid):
    name = "opskit-linux-x64"
    directory = artifacts / name
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    delta = f"{name}-from-v{base}.tar.xz"
    manifest["directory"][delta] = write_artifact(directory / delta)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    if valid:
        assert {delta, delta + ".sha256"} <= ra.assets(artifacts, 7).keys()
        (directory / delta).write_bytes(b"injected bad digest")
        with pytest.raises(ValueError, match="metadata mismatch|checksum mismatch"):
            ra.assets(artifacts, 7)
    else:
        with pytest.raises(ValueError, match="delta name or base version mismatch"):
            ra.assets(artifacts, 7)


def test_previous_build_ignores_current_newer_unpublished_and_non_build_tags():
    fake = FakeGH()
    fake.other_releases = [
        {"tag_name": tag, "draft": draft, "prerelease": prerelease}
        for tag, draft, prerelease in (("v1", False, False), ("v2", False, False),
                                       ("v6", False, True), ("v5", True, False),
                                       ("v7", False, False), ("v12", False, False), ("latest", False, False))
    ]
    assert ra.previous_build(fake.api(), 7) == 2
    assert ra.previous_build(fake.api(), 1) is None


@pytest.mark.parametrize("bad_digest", [False, True])
def test_base_download_uses_exact_published_asset_and_authenticated_digest(tmp_path, bad_digest):
    import hashlib

    payload = b"authorized by the release API"
    filename = "opskit-linux-x64.tar.xz"
    output = tmp_path / "verified.tar.xz"
    digest = "sha256:" + ("0" * 64 if bad_digest else hashlib.sha256(payload).hexdigest())
    listing = [{"name": f"{name}.tar.xz", "size": len(payload), "state": "uploaded", "digest": digest}
               for name in ra.DIRECTORY_MATRIX]

    class BaseAPI:
        def request(self, method, endpoint):
            assert method == "GET"
            if endpoint == "releases/tags/v4":
                return {"tag_name": "v4", "id": 28, "draft": False, "prerelease": False}
            assert endpoint == "releases/28/assets?per_page=100"
            return listing

        def download(self, tag, selected, destination):
            assert (tag, selected) == ("v4", filename)
            destination.write_bytes(payload)

    if bad_digest:
        with pytest.raises(ValueError, match="checksum mismatch"):
            ra.fetch_base(BaseAPI(), 4, "opskit-linux-x64", output)
        assert not output.exists()
    else:
        assert ra.fetch_base(BaseAPI(), 4, "opskit-linux-x64", output)
        assert output.read_bytes() == payload


def test_validator_matrix_matches_actual_workflow():
    workflow = yaml.safe_load((Path(SPEC.origin).parents[1] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    builds = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {build["artifact"] for build in builds} == set(ra.MATRIX)
