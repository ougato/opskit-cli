"""Offline acquisition builds reject changed inputs before a daemon call."""
import hashlib
import io
import json
import os
import tarfile

import pytest

from core import plugin_fetch_image as image, plugin_runtime as runtime


@pytest.fixture
def packages(tmp_path, monkeypatch):
    root = tmp_path / "archives"
    root.mkdir()
    body = b"synthetic archive bytes"
    package = {"file": "tool_1_amd64.deb", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    (root / package["file"]).write_bytes(body)
    monkeypatch.setattr(image, "LOCK", {"packages": [package]})
    return root, package, body


@pytest.mark.parametrize("change", ["missing", "modified", "truncated", "directory", "symlink", "hardlink", "budget"])
def test_changed_package_refused_before_daemon(packages, monkeypatch, change):
    root, package, body = packages
    path = root / package["file"]
    if change == "missing":
        path.unlink()
    elif change == "modified":
        path.write_bytes(b"x" * len(body))
    elif change == "truncated":
        path.write_bytes(body[:-1])
    elif change == "directory":
        path.unlink()
        path.mkdir()
    elif change == "symlink":
        path.unlink()
        target = root / "target"
        target.write_bytes(body)
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
    elif change == "hardlink":
        os.link(path, root / "alias")
    else:
        monkeypatch.setattr(image, "MAX_PACKAGES_BYTES", len(body) - 1)
    monkeypatch.setattr(runtime, "_command_output", lambda *args, **kwargs: pytest.fail("unverified package reached daemon"))
    with pytest.raises((OSError, ValueError)):
        image.build(["docker"], root)


def test_offline_context_is_exact_and_deterministic(packages, monkeypatch):
    root, package, body = packages
    (root / "private-do-not-copy").write_text("fixture secret")
    contexts, tags = [], []
    result = "sha256:" + "a" * 64
    lock_digest = hashlib.sha256(json.dumps(image.LOCK, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    def output(command, **kwargs):
        if command[1] == "build":
            assert "--network=none" in command and "--pull=false" in command and "--platform=linux/amd64" in command
            contexts.append(kwargs["stdin"].read())
            tags.append(command[command.index("--tag") + 1])
            return ""
        if command[-1] == image.BASE_REFERENCE:
            return json.dumps({"id": image.BASE_IMAGE, "architecture": "amd64"})
        if command[-1] == result:
            return json.dumps({"architecture": "amd64", "policy": str(image.POLICY), "lock": lock_digest,
                               "worker": hashlib.sha256(image.fetch_source()).hexdigest(),
                               "sources": image.code_digest(image.fetch_snapshot())})
        return result
    monkeypatch.setattr(runtime, "_command_output", output)
    assert image.build(["docker"], root) == result
    assert image.build(["docker"], root) == result
    assert contexts[0] == contexts[1] and tags[0] == tags[1]
    with tarfile.open(fileobj=io.BytesIO(contexts[0])) as archive:
        assert set(archive.getnames()) == {"Dockerfile", "fetch.py", "packages/" + package["file"],
                                          *(n for n in image.fetch_snapshot() if n != "core/plugin_fetch_worker.py")}
        assert archive.extractfile("fetch.py").read() == image.fetch_source()
        for name, content in image.fetch_snapshot().items():
            if name != "core/plugin_fetch_worker.py":
                assert archive.extractfile(name).read() == content
        assert archive.extractfile("packages/" + package["file"]).read() == body
        recipe = archive.extractfile("Dockerfile").read().decode()
        assert "FROM " + image.BASE_IMAGE in recipe
        assert "USER 65534:65534" in recipe
        assert "--no-download check" in recipe
        assert image.INSTALLED_PACKAGES_SHA256 in recipe
        assert "GIT_CONFIG_GLOBAL=/dev/null" in recipe


@pytest.mark.parametrize("actual", [{"id": "sha256:" + "b" * 64, "architecture": "amd64"},
                                     {"id": image.BASE_IMAGE, "architecture": "arm64"}])
def test_wrong_base_refused_before_build(packages, monkeypatch, actual):
    def output(command, **kwargs):
        assert command[1:3] == ["image", "inspect"]
        return json.dumps(actual)
    monkeypatch.setattr(runtime, "_command_output", output)
    with pytest.raises(ValueError, match="base identity or architecture"):
        image.build(["docker"], packages[0])


def test_reviewed_lock_budgets_and_origins():
    assert image.LOCK["base_image"] == image.BASE_IMAGE
    assert len(image.LOCK["packages"]) == len({p["file"] for p in image.LOCK["packages"]}) == 35
    assert sum(p["bytes"] for p in image.LOCK["packages"]) < image.MAX_PACKAGES_BYTES
    for package in image.LOCK["packages"]:
        assert "/" not in package["file"] and "\\" not in package["file"]
        assert 0 < package["bytes"] < image.MAX_PACKAGE_BYTES
        assert len(package["sha256"]) == 64
        assert package["url"].startswith(("https://deb.debian.org/debian/pool/", "https://deb.debian.org/debian-security/pool/"))


@pytest.mark.parametrize("fault", ["tag", "label", "architecture"])
def test_built_image_must_have_expected_identity(packages, monkeypatch, fault):
    result = "sha256:" + "a" * 64
    lock_digest = hashlib.sha256(json.dumps(image.LOCK, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    def output(command, **kwargs):
        if command[1] == "build":
            return ""
        if command[-1] == image.BASE_REFERENCE:
            return json.dumps({"id": image.BASE_IMAGE, "architecture": "amd64"})
        if command[-1] == result:
            return json.dumps({"architecture": "arm64" if fault == "architecture" else "amd64",
                               "policy": str(image.POLICY), "lock": "b" * 64 if fault == "label" else lock_digest,
                               "worker": hashlib.sha256(image.fetch_source()).hexdigest(),
                               "sources": image.code_digest(image.fetch_snapshot())})
        return "untrusted-tag" if fault == "tag" else result
    monkeypatch.setattr(runtime, "_command_output", output)
    with pytest.raises(ValueError, match="image identity"):
        image.build(["docker"], packages[0])
