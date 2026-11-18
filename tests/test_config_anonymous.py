"""Tests for Linux initial configuration publication by open descriptor."""
import errno
import os
import stat
import subprocess
import sys

import pytest

from core import config_anonymous as publisher


pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux O_TMPFILE")


@pytest.fixture
def supported(tmp_path):
    probe = tmp_path / "probe"
    if not publisher.publish_if_supported(probe, b"probe"):
        pytest.skip("filesystem does not support anonymous file publication")
    probe.unlink()
    return tmp_path


def test_publish_file_complete_and_private(supported):
    path = supported / "common.yaml"
    assert publisher.publish_if_supported(path, b"language: zh\n")
    assert path.read_bytes() == b"language: zh\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1


def test_competing_processes_publish_exactly_one_complete_file(supported):
    path = supported / "common.yaml"
    script = (
        "import sys; from pathlib import Path; "
        "from core.config_anonymous import publish_if_supported; "
        "sys.stdin.readline(); "
        "exec('try:\\n publish_if_supported(Path(sys.argv[1]), sys.argv[2].encode())\\n"
        "except FileExistsError:\\n sys.exit(17)')"
    )
    payloads = [f"writer-{index}-" + "x" * 16384 for index in range(8)]
    children = []
    try:
        for payload in payloads:
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(path), payload],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            ))
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        outcomes = [child.communicate(timeout=20) for child in children]
        codes = [child.returncode for child in children]
        assert codes.count(0) == 1, (codes, outcomes)
        assert codes.count(17) == len(children) - 1, (codes, outcomes)
        assert path.read_text() == payloads[codes.index(0)]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert list(supported.iterdir()) == [path]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()


def test_restrictive_umask_does_not_prevent_reading_the_config(supported):
    path = supported / "common.yaml"
    script = (
        "import os, sys; from pathlib import Path; "
        "from core.config_anonymous import publish_if_supported; "
        "os.umask(0o777); "
        "assert publish_if_supported(Path(sys.argv[1]), b'defaults')"
    )
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=10)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == b"defaults"


def test_interrupted_before_link_leaves_no_artifact_and_can_retry(supported):
    path = supported / "common.yaml"
    script = (
        "import os, sys; from pathlib import Path; "
        "import core.config_anonymous as p; "
        "p._link_descriptor = lambda *args: os._exit(77); "
        "p.publish_if_supported(Path(sys.argv[1]), b'defaults')"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path)], timeout=10)
    assert result.returncode == 77
    assert not list(supported.iterdir())
    assert publisher.publish_if_supported(path, b"defaults")
    assert path.read_bytes() == b"defaults"


def test_fsync_failure_does_not_remove_published_file(supported, monkeypatch):
    path = supported / "common.yaml"
    fsync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        fsync(fd)

    monkeypatch.setattr(publisher.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory fsync failed"):
        publisher.publish_if_supported(path, b"defaults")
    assert path.read_bytes() == b"defaults"


def test_configuration_recovers_after_publish_fsync_failure(supported, monkeypatch):
    from core.config import ensure_config, get_config_path

    fsync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        fsync(fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(publisher.os, "fsync", fail_directory)
        with pytest.raises(OSError, match="directory fsync failed"):
            ensure_config()
    path = get_config_path()
    original = path.read_bytes()
    assert ensure_config()["language"]
    assert path.read_bytes() == original


def test_unsupported_link_returns_false_without_residue(supported, monkeypatch):
    path = supported / "common.yaml"

    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, "unsupported")

    monkeypatch.setattr(publisher, "_link_descriptor", unsupported)
    assert publisher.publish_if_supported(path, b"defaults") is False
    assert not list(supported.iterdir())


def test_other_link_error_is_not_treated_as_unsupported(supported, monkeypatch):
    path = supported / "common.yaml"

    def denied(*args):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(publisher, "_link_descriptor", denied)
    with pytest.raises(PermissionError):
        publisher.publish_if_supported(path, b"defaults")
    assert not list(supported.iterdir())


def test_link_to_unrelated_file_is_detected_and_preserved(supported, monkeypatch):
    path = supported / "common.yaml"
    unrelated = supported / "unrelated"
    unrelated.write_bytes(b"other user data")

    def link_unrelated(source, directory, name):
        os.link(unrelated, name, dst_dir_fd=directory)

    monkeypatch.setattr(publisher, "_link_descriptor", link_unrelated)
    with pytest.raises(ValueError, match="published configuration identity changed"):
        publisher.publish_if_supported(path, b"defaults")
    assert unrelated.read_bytes() == b"other user data"
    assert path.read_bytes() == b"other user data"


def test_parent_swap_preserves_competitor(supported, monkeypatch):
    parent = supported / "config"
    parent.mkdir()
    moved = supported / "moved"
    path = parent / "common.yaml"
    original = publisher._link_descriptor

    def swap_parent(source, directory, name):
        parent.rename(moved)
        parent.mkdir()
        path.write_bytes(b"other user data")
        original(source, directory, name)

    monkeypatch.setattr(publisher, "_link_descriptor", swap_parent)
    with pytest.raises(ValueError, match="directory changed"):
        publisher.publish_if_supported(path, b"defaults")
    assert path.read_bytes() == b"other user data"
    assert (moved / "common.yaml").read_bytes() == b"defaults"
