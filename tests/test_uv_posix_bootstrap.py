"""Fixed archive bootstrap refuses unsafe inputs and preserves existing files."""
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import os
from types import SimpleNamespace

import pytest

from core import process
from software.base import InstallError
from software.recipes.python import common, uv_bootstrap as bootstrap


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from core import platform
    destination = tmp_path / "bin/uv"
    monkeypatch.setattr(common, "uv_bin_path", lambda: destination)
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux", arch="x86_64"))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "curl" if name == "curl" else None)
    return destination


def download(monkeypatch, entries, *, valid_digest=True, after=None):
    calls = []
    def capture(command, **options):
        calls.append((command, options))
        path = Path(command[command.index("--output") + 1])
        with tarfile.open(path, "w:gz") as archive:
            for name, data in entries:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        if valid_digest:
            monkeypatch.setitem(bootstrap.UV_ASSET_SHA256, "uv-x86_64-unknown-linux-musl.tar.gz",
                                hashlib.sha256(path.read_bytes()).hexdigest())
        if after:
            after()
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(process, "capture", capture)
    return calls


def test_valid_archive_publishes_exclusively_and_cleans_work(setup, monkeypatch):
    calls = download(monkeypatch, [("uv-release/uv", b"inert")])
    assert bootstrap.ensure_uv_archive() == str(setup)
    assert setup.read_bytes() == b"inert"
    assert list(setup.parent.iterdir()) == [setup]
    command, options = calls[0]
    assert "/download/0.13.0/" in command[-1]
    assert command[command.index("--proto-redir") + 1] == "=https"
    assert options["stdin"] == subprocess.DEVNULL and options["max_bytes"] == 16384


@pytest.mark.parametrize("entries,valid_digest", [
    ([("uv-release/uv", b"untrusted")], False),
    ([("uv-release/../escape", b"bad")], True),
    ([("uv-release/not-uv", b"bad")], True),
])
def test_bad_integrity_or_layout_never_publishes(setup, monkeypatch, entries, valid_digest):
    download(monkeypatch, entries, valid_digest=valid_digest)
    with pytest.raises(InstallError):
        bootstrap.ensure_uv_archive()
    assert not setup.exists() and not list(setup.parent.iterdir())


def test_competing_destination_preserved(setup, monkeypatch):
    download(monkeypatch, [("uv-release/uv", b"inert")], after=lambda: setup.write_bytes(b"external"))
    with pytest.raises(InstallError):
        bootstrap.ensure_uv_archive()
    assert setup.read_bytes() == b"external"
    assert list(setup.parent.iterdir()) == [setup]


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancel_clean_private_work(setup, monkeypatch, cancel):
    def fail(command, **options):
        raise KeyboardInterrupt() if cancel else subprocess.TimeoutExpired(command, options["timeout"])
    monkeypatch.setattr(process, "capture", fail)
    with pytest.raises(KeyboardInterrupt if cancel else InstallError):
        bootstrap.ensure_uv_archive()
    assert not setup.exists() and not list(setup.parent.iterdir())


@pytest.mark.parametrize("os_type,arch", [("linux", "x86_64"), ("linux", "aarch64"),
    ("linux", "x86"), ("linux", "armv7"), ("linux", "armv6"),
    ("darwin", "x86_64"), ("darwin", "aarch64"), ("windows", "x86_64"),
    ("windows", "aarch64"), ("windows", "x86")])
def test_declared_artifacts_have_fixed_digest(os_type, arch):
    name = bootstrap.asset_name(SimpleNamespace(os_type=os_type, arch=arch))
    assert len(bootstrap.UV_ASSET_SHA256[name]) == 64


def test_existing_shared_inode_is_rejected_without_download(setup, monkeypatch):
    setup.parent.mkdir()
    original = setup.parent / "external"
    original.write_bytes(b"external data")
    original.chmod(0o755)
    os.link(original, setup)
    monkeypatch.setattr(process, "capture", lambda *args, **kwargs: pytest.fail("unsafe private uv triggered download"))
    with pytest.raises(InstallError, match="refusing to overwrite"):
        bootstrap.ensure_uv_archive()
    assert original.read_bytes() == setup.read_bytes() == b"external data"


@pytest.mark.parametrize("os_type,arch", [("windows", "armv7"), ("darwin", "x86"), ("linux", "unknown")])
def test_unknown_platform_artifact_is_rejected(os_type, arch):
    with pytest.raises(InstallError, match="unavailable"):
        bootstrap.asset_name(SimpleNamespace(os_type=os_type, arch=arch))
